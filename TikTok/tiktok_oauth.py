import json
import os
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken


BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"
TOKEN_URL = f"{BASE_URL}/tt_user/oauth2/token/"
REFRESH_URL = f"{BASE_URL}/tt_user/oauth2/refresh_token/"


class TikTokOAuthError(Exception):
    pass


def _credentials():
    app_id = os.getenv("TIKTOK_APP_ID", "").strip()
    app_secret = os.getenv("TIKTOK_APP_SECRET", "").strip()

    if not app_id or not app_secret:
        raise TikTokOAuthError("TikTok application credentials are missing.")

    return app_id, app_secret


def _cipher():
    key = os.getenv("TIKTOK_TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise TikTokOAuthError("TikTok token encryption key is missing.")

    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError):
        raise TikTokOAuthError("TikTok token encryption key is invalid.")


def encrypt_token(token):
    if not token:
        raise TikTokOAuthError("Cannot encrypt an empty token.")
    return _cipher().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token):
    if not encrypted_token:
        raise TikTokOAuthError("Encrypted token is missing.")

    try:
        return _cipher().decrypt(
            encrypted_token.encode("utf-8")
        ).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        raise TikTokOAuthError("Token decryption failed.")


def _post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TikTokOAuthError(
            f"TikTok returned HTTP {exc.code}."
        ) from None
    except (URLError, TimeoutError):
        raise TikTokOAuthError(
            "Could not reach TikTok. Credentials were not changed."
        ) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TikTokOAuthError(
            "TikTok returned an unreadable response."
        ) from None

    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message", "Unknown TikTok API error") \
            if isinstance(result, dict) else "Invalid API response"
        raise TikTokOAuthError(f"TikTok authorization failed: {message}")

    data = result.get("data")
    if not isinstance(data, dict):
        raise TikTokOAuthError("TikTok response did not contain token data.")

    return data


def _validate_token_response(data):
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")

    if not access_token or not refresh_token:
        raise TikTokOAuthError(
            "TikTok did not return both required tokens."
        )

    try:
        access_seconds = int(data["expires_in"])
        refresh_seconds = int(data["refresh_token_expires_in"])
    except (KeyError, TypeError, ValueError):
        raise TikTokOAuthError(
            "TikTok returned invalid token expiration information."
        )

    if access_seconds <= 0 or refresh_seconds <= 0:
        raise TikTokOAuthError("TikTok returned invalid token lifetimes.")

    now = datetime.now(timezone.utc)

    return {
        "access_token_encrypted": encrypt_token(access_token),
        "refresh_token_encrypted": encrypt_token(refresh_token),
        "access_expires_at": now + timedelta(seconds=access_seconds),
        "refresh_expires_at": now + timedelta(seconds=refresh_seconds),
        "open_id": data.get("open_id"),
        "scope": data.get("scope"),
        "token_type": data.get("token_type", "Bearer"),
    }


def exchange_authorization_code(auth_code, redirect_uri):
    if not auth_code or not redirect_uri:
        raise TikTokOAuthError(
            "Authorization code or redirect URI is missing."
        )

    app_id, app_secret = _credentials()

    data = _post_json(TOKEN_URL, {
        "client_id": app_id,
        "client_secret": app_secret,
        "grant_type": "authorization_code",
        "auth_code": auth_code,
        "redirect_uri": redirect_uri,
    })

    return _validate_token_response(data)


def refresh_access_token(refresh_token):
    if not refresh_token:
        raise TikTokOAuthError("Refresh token is missing.")

    app_id, app_secret = _credentials()

    data = _post_json(REFRESH_URL, {
        "client_id": app_id,
        "client_secret": app_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })

    return _validate_token_response(data)

# ---------------------------------------------------------------------------
# PostgreSQL token persistence
# The connection is supplied by the Flask application.
# ---------------------------------------------------------------------------

def save_token_bundle(conn, tokens):
    """Persist an encrypted TikTok token bundle."""
    required = (
        "access_token_encrypted",
        "refresh_token_encrypted",
        "access_expires_at",
        "refresh_expires_at",
    )
    if any(not tokens.get(key) for key in required):
        raise TikTokOAuthError("Incomplete token bundle; nothing was saved.")

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO beautyrocket_tiktok_oauth (
                account_key,
                access_token_encrypted,
                refresh_token_encrypted,
                access_expires_at,
                refresh_expires_at,
                open_id,
                granted_scope,
                last_refreshed_at,
                updated_at
            )
            VALUES (
                'primary', %s, %s, %s, %s, %s, %s, NOW(), NOW()
            )
            ON CONFLICT (account_key) DO UPDATE SET
                access_token_encrypted = EXCLUDED.access_token_encrypted,
                refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                access_expires_at = EXCLUDED.access_expires_at,
                refresh_expires_at = EXCLUDED.refresh_expires_at,
                open_id = EXCLUDED.open_id,
                granted_scope = EXCLUDED.granted_scope,
                last_refreshed_at = NOW(),
                updated_at = NOW()
        """, (
            tokens["access_token_encrypted"],
            tokens["refresh_token_encrypted"],
            tokens["access_expires_at"],
            tokens["refresh_expires_at"],
            tokens.get("open_id"),
            tokens.get("scope"),
        ))


def load_token_bundle(conn):
    """Load and decrypt the stored TikTok credentials."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT *
            FROM beautyrocket_tiktok_oauth
            WHERE account_key = 'primary'
        """)
        row = cur.fetchone()

    if not row:
        return None

    result = dict(row)
    result["access_token"] = decrypt_token(
        result["access_token_encrypted"]
    )
    result["refresh_token"] = decrypt_token(
        result["refresh_token_encrypted"]
    )
    return result


def refresh_stored_token(connect):
    """
    Refresh the primary account token when it is within five minutes
    of expiration.

    connect must be the Flask application's _db_connect function.
    The row lock prevents concurrent workers from rotating the same
    refresh token simultaneously.
    """
    from datetime import datetime, timedelta, timezone

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM beautyrocket_tiktok_oauth
                WHERE account_key = 'primary'
                FOR UPDATE
            """)
            row = cur.fetchone()

            if not row:
                raise TikTokOAuthError(
                    "No authorized TikTok account is stored."
                )

            now = datetime.now(timezone.utc)
            expires_at = row["access_expires_at"]

            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at > now + timedelta(minutes=5):
                return {
                    "status": "valid",
                    "access_expires_at": expires_at.isoformat(),
                }

            refresh_expires_at = row["refresh_expires_at"]
            if refresh_expires_at.tzinfo is None:
                refresh_expires_at = refresh_expires_at.replace(
                    tzinfo=timezone.utc
                )

            if refresh_expires_at <= now:
                raise TikTokOAuthError(
                    "TikTok refresh token has expired. Reauthorization required."
                )

            current_refresh_token = decrypt_token(
                row["refresh_token_encrypted"]
            )

            # Network failure or API rejection raises an exception.
            # The transaction then rolls back, preserving stored tokens.
            replacement = refresh_access_token(current_refresh_token)

            save_token_bundle(conn, replacement)

            return {
                "status": "refreshed",
                "access_expires_at": replacement[
                    "access_expires_at"
                ].isoformat(),
                "refresh_expires_at": replacement[
                    "refresh_expires_at"
                ].isoformat(),
            }

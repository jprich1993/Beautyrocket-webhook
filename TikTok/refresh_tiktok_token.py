import logging
import os
import sys

import psycopg
from psycopg.rows import dict_row

from TikTok.tiktok_oauth import (
    TikTokOAuthError,
    refresh_stored_token,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("beautyrocket.tiktok.refresh")


def database_connect():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    return psycopg.connect(database_url, row_factory=dict_row)


def main():
    required = (
        "TIKTOK_APP_ID",
        "TIKTOK_APP_SECRET",
        "TIKTOK_TOKEN_ENCRYPTION_KEY",
        "DATABASE_URL",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]

    if missing:
        raise RuntimeError(
            "Missing required environment configuration: "
            + ", ".join(missing)
        )

    result = refresh_stored_token(database_connect)

    logger.info(
        "TikTok token check completed: status=%s access_expires_at=%s refresh_expires_at=%s",
        result.get("status"),
        result.get("access_expires_at", "unchanged"),
        result.get("refresh_expires_at", "unchanged"),
    )


if __name__ == "__main__":
    try:
        main()
    except (TikTokOAuthError, Exception):
        logger.exception("TikTok scheduled token check failed.")
        sys.exit(1)

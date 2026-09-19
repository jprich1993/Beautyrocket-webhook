import json
import os
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, redirect, render_template_string, request, jsonify, send_file, session, url_for
from functools import wraps
from werkzeug.security import check_password_hash
import secrets
import hmac

# PostgreSQL is the cloud persistence layer. The JSON fallback keeps the app
# usable locally when DATABASE_URL is not configured.
try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:
    psycopg = None
    dict_row = None
    Jsonb = None

QUEUE_FILE = Path(os.getenv("BEAUTYROCKET_COMMENT_QUEUE_FILE", "beautyrocket_comment_queue.json"))
STATS_FILE = Path(os.getenv("BEAUTYROCKET_STATS_FILE", "beautyrocket_production_stats.json"))
BACKGROUND_FILE = Path(os.getenv("BEAUTYROCKET_BACKGROUND_FILE", "beautyrocket_background.png"))

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_ENABLED = bool(DATABASE_URL)

if DB_ENABLED and psycopg is None:
    raise RuntimeError(
        "DATABASE_URL is configured, but psycopg is not installed. "
        "Add psycopg[binary] to requirements.txt and redeploy."
    )

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

QUEUE_TABLE = "beautyrocket_dashboard_queue"
STATS_TABLE = "beautyrocket_dashboard_stats"

app = Flask(__name__)

# Cloud dashboard authentication.
# Credentials are supplied through Render environment variables.
# Never hard-code them in this source file.
app.secret_key = os.getenv("BEAUTYROCKET_SESSION_SECRET") or secrets.token_urlsafe(48)
WEB_USERNAME = os.getenv("BEAUTYROCKET_WEB_USERNAME", "")
WEB_PASSWORD_HASH = os.getenv("BEAUTYROCKET_WEB_PASSWORD_HASH", "")
BOT_API_KEY = os.getenv("BEAUTYROCKET_BOT_API_KEY", "").strip()

if not WEB_USERNAME or not WEB_PASSWORD_HASH:
    raise RuntimeError(
        "BEAUTYROCKET_WEB_USERNAME and BEAUTYROCKET_WEB_PASSWORD_HASH "
        "must be configured in the environment before starting the cloud dashboard."
    )

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("BEAUTYROCKET_SECURE_COOKIE", "0") == "1",
)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def valid_csrf(token):
    expected = session.get("csrf_token")
    return bool(expected and token and secrets.compare_digest(expected, token))

LOGIN_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f8e9df">
<title>Beautyrocket — Login</title>
<style>
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
        font-family: Arial, Helvetica, sans-serif;
        margin: 0;
        color: #222;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 22px;
        background: #f8eee6 url('/background.png') center center / cover fixed no-repeat;
    }
    body::before {
        content: "";
        position: fixed;
        inset: 0;
        background: rgba(255, 249, 244, .66);
        backdrop-filter: blur(2px);
        z-index: -1;
    }
    .card {
        width: min(430px, 100%);
        background: rgba(255,255,255,.90);
        border: 1px solid rgba(255,255,255,.9);
        border-radius: 24px;
        padding: 30px;
        box-shadow: 0 18px 55px rgba(91,64,57,.16);
        backdrop-filter: blur(10px);
    }
    .brand {
        font-family: Georgia, 'Times New Roman', serif;
        font-size: 50px;
        text-align: center;
        color: #18232c;
        margin: 0;
    }
    .subtitle {
        text-align: center;
        color: #9a7e7e;
        font-size: 12px;
        letter-spacing: 3px;
        margin: 8px 0 25px;
    }
    label { display: block; font-size: 12px; font-weight: 700; margin: 14px 0 7px; color: #5f5757; }
    input {
        width: 100%;
        padding: 14px;
        border: 1px solid #dfd2d0;
        border-radius: 12px;
        font-size: 16px;
        background: rgba(255,255,255,.92);
    }
    button {
        width: 100%;
        margin-top: 20px;
        border: 0;
        border-radius: 13px;
        padding: 15px;
        font-size: 15px;
        font-weight: 800;
        cursor: pointer;
        background: #c66f7c;
        color: white;
    }
    .error {
        margin-top: 14px;
        padding: 11px 12px;
        border-radius: 10px;
        background: #fff0f1;
        color: #a04452;
        font-size: 13px;
    }
</style>
</head>
<body>
<div class="card">
    <h1 class="brand">Beautyrocket</h1>
    <div class="subtitle">SECURE DASHBOARD LOGIN</div>
    <form method="post">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label for="username">USERNAME</label>
        <input id="username" name="username" autocomplete="username" required>
        <label for="password">PASSWORD</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button type="submit">LOG IN</button>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
    </form>
</div>
</body>
</html>
"""

HTML = """
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f8e9df">
<title>Beautyrocket — Comment Review</title>
<style>
    * { box-sizing: border-box; }
    html { min-height: 100%; }
    body {
        font-family: Arial, Helvetica, sans-serif;
        margin: 0;
        color: #222;
        min-height: 100vh;
        background: #f8eee6 url('/background.png') center center / cover fixed no-repeat;
    }
    body::before {
        content: "";
        position: fixed;
        inset: 0;
        background: rgba(255, 249, 244, .58);
        backdrop-filter: blur(1px);
        z-index: -1;
    }
    .wrap { max-width: 900px; margin: 0 auto; padding: 24px 16px 42px; }
    .header { text-align: center; margin: 6px 0 22px; }
    .brand { font-family: Georgia, 'Times New Roman', serif; font-size: clamp(38px, 8vw, 64px); line-height: .95; margin: 0; color: #18232c; letter-spacing: -1px; }
    .title { margin: 9px 0 4px; font-size: 14px; letter-spacing: 5px; color: #26333b; font-weight: 700; }
    .tagline { margin: 0; color: #9a7e7e; font-size: 12px; letter-spacing: 3px; }

    .production {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 12px;
        margin-bottom: 18px;
    }
    .stat {
        background: rgba(255,255,255,.84);
        border: 1px solid rgba(255,255,255,.85);
        border-radius: 18px;
        padding: 16px 12px;
        text-align: center;
        box-shadow: 0 8px 28px rgba(91, 64, 57, .10);
        backdrop-filter: blur(8px);
    }
    .stat-number { font-size: 31px; font-weight: 800; color: #c66f7c; line-height: 1; }
    .stat-label { margin-top: 7px; font-size: 11px; letter-spacing: 1.4px; text-transform: uppercase; color: #6f6666; font-weight: 700; }
    .run-line { text-align: center; color: #746a6a; font-size: 12px; margin: 0 0 20px; }
    .run-line strong { color: #b75e6d; }

    .queue-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
        margin: 0 4px 10px;
    }
    .queue-head h2 { font-size: 18px; margin: 0; color: #28343a; }
    .pending-pill { background: #f2ccd1; color: #7d3543; border-radius: 999px; padding: 7px 12px; font-size: 12px; font-weight: 800; }

    .card {
        background: rgba(255,255,255,.86);
        border: 1px solid rgba(255,255,255,.88);
        border-radius: 22px;
        padding: 20px;
        margin: 14px 0;
        box-shadow: 0 10px 34px rgba(91, 64, 57, .12);
        backdrop-filter: blur(9px);
    }
    .score { font-size: 34px; font-weight: 800; color: #c86d7c; }
    .meta { color: #625b5b; font-size: 13px; line-height: 1.55; }
    .comment { background: rgba(245,244,244,.9); border-radius: 14px; padding: 15px; margin: 15px 0; line-height: 1.5; white-space: pre-wrap; font-size: 16px; }
    .buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
    button, a.btn { display: block; width: 100%; box-sizing: border-box; border: 0; border-radius: 13px; padding: 14px 10px; font-size: 14px; font-weight: 800; text-align: center; text-decoration: none; cursor: pointer; }
    .copy { background: #20262b; color: white; }
    .open { background: #efb1b9; color: #402b30; }
    .skip { background: #eeeeee; color: #555; }
    .done { background: #cfeeda; color: #155b2b; }
    .caption { color: #625b5b; font-size: 13px; margin-top: 13px; line-height: 1.45; }
    .empty { background: rgba(255,255,255,.84); padding: 40px 22px; border-radius: 22px; text-align: center; color: #666; box-shadow: 0 10px 34px rgba(91,64,57,.10); }
    .empty strong { display: block; margin-top: 9px; color: #d07a87; letter-spacing: 3px; font-size: 12px; }
    .refresh { display: inline-block; border: 0; background: rgba(255,255,255,.75); color: #6f5d60; border-radius: 999px; padding: 8px 13px; font-size: 12px; font-weight: 700; cursor: pointer; }
    .footer { text-align: center; color: #8b7d7d; font-size: 11px; margin-top: 22px; letter-spacing: 1px; }

    @media (max-width: 560px) {
        .wrap { padding: 18px 11px 32px; }
        .production { gap: 7px; }
        .stat { padding: 13px 7px; border-radius: 15px; }
        .stat-number { font-size: 25px; }
        .stat-label { font-size: 9px; letter-spacing: .8px; }
        .card { padding: 16px; border-radius: 18px; }
        .buttons { grid-template-columns: 1fr; }
        button, a.btn { padding: 14px; }
        .score { font-size: 31px; }
    }
</style>
</head>
<body>
<div class="wrap">
    <header class="header">
        <h1 class="brand">Beautyrocket</h1>
        <div class="title">COMMENT AND LIKE REVIEW</div>
        <div class="tagline">DISCOVER · ENGAGE · GROW</div>
    </header>

    <section class="production" aria-label="Production statistics">
        <div class="stat">
            <div class="stat-number" id="total-opportunities">{{ stats.total_comment_suggestions }}</div>
            <div class="stat-label">TOTAL POST ENGAGEMENTS FOUND</div>
        </div>
        <div class="stat">
            <div class="stat-number" id="pending-count">{{ pending|length }}</div>
            <div class="stat-label">PENDING POST ENGAGEMENT</div>
        </div>
        <div class="stat">
            <div class="stat-number" id="ai-comments">{{ stats.pending_ai_comments }}</div>
            <div class="stat-label">AI Comments Available</div>
        </div>
    </section>

    <div class="run-line">
        Last execution: <strong id="last-run-comments">+{{ stats.last_run_new_queue_items }}</strong> new opportunities ·
        <strong>user decides</strong> like / comment / both / neither
        {% if stats.last_run_at_utc %} · {{ stats.last_run_at_utc[:19].replace('T', ' ') }} UTC{% endif %}
    </div>

    <div class="queue-head">
        <h2>Engagement Opportunities</h2>
        <span class="pending-pill">{{ pending|length }} pending</span>
    </div>

    {% if pending %}
        {% for item in pending %}
        <div class="card">
            <div class="score">{{ item.best_score }}/100</div>
            <div class="meta"><strong>{{ (item.opportunity_type or item.target_action or 'ENGAGEMENT OPPORTUNITY').replace('_', ' ') }}</strong></div>
            <div class="meta">
                Buyer: {{ item.buyer_score }} ({{ item.buyer_band.replace('_', ' ') }}) ·
                Follower: {{ item.follower_score }} ({{ item.follower_band.replace('_', ' ') }})
            </div>
            <div class="meta">Category: {{ item.categories|join(', ') }}</div>

            {% if item.suggested_comment %}
            <div class="comment-label">AI SUGGESTED COMMENT — USE, EDIT, OR IGNORE</div>
            <div class="comment" id="comment-{{ item.queue_id }}">{{ item.suggested_comment }}</div>
            {% else %}
            <div class="comment-label">NO AI COMMENT AVAILABLE — USER MAY WRITE THEIR OWN</div>
            {% endif %}

            <div class="buttons">
                {% if item.suggested_comment %}<button class="copy" onclick="copyComment('comment-{{ item.queue_id }}', this)">📋 COPY AI COMMENT</button>{% endif %}
                <a class="btn open" href="{{ item.permalink }}" target="_blank" rel="noopener">🎬 OPEN REEL</a>
                <form method="post" action="/done/{{ item.queue_id }}">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <button class="done" type="submit">✓ DONE — ENGAGEMENT HANDLED</button>
                </form>
                <form method="post" action="/skip/{{ item.queue_id }}">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <button class="skip" type="submit">SKIP — NOT USING</button>
                </form>
            </div>

            {% if item.caption %}
            <div class="caption">{{ item.caption[:300] }}{% if item.caption|length > 300 %}…{% endif %}</div>
            {% endif %}
        </div>
        {% endfor %}
    {% else %}
        <div class="empty">No pending engagement opportunities right now.<strong>YOU'RE ALL CAUGHT UP!</strong></div>
    {% endif %}

    <div style="text-align:center; margin-top:18px; display:flex; gap:8px; justify-content:center; flex-wrap:wrap;">
        <button class="refresh" onclick="location.reload()">↻ REFRESH DASHBOARD</button>
        <form method="post" action="/logout" style="display:inline;">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button class="refresh" type="submit">LOG OUT</button>
        </form>
    </div>
    <div class="footer">BEAUTYROCKET HUMAN ENGAGEMENT DASHBOARD</div>
</div>

<script>
async function copyComment(id, button) {
    const text = document.getElementById(id).innerText;
    try {
        await navigator.clipboard.writeText(text);
        const old = button.innerText;
        button.innerText = "✓ COPIED";
        setTimeout(() => button.innerText = old, 1400);
    } catch (e) {
        alert("Copy failed. Please long-press/select the comment manually.");
    }
}

async function refreshStats() {
    try {
        const response = await fetch('/stats', { cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        document.getElementById('total-opportunities').innerText = data.total_comment_suggestions;
        document.getElementById('pending-count').innerText = data.pending_count;
        document.getElementById('ai-comments').innerText = data.pending_ai_comments || 0;
        document.getElementById('last-run-comments').innerText = '+' + (data.last_run_new_queue_items || 0);
    } catch (e) { /* local dashboard may briefly be unavailable */ }
}
setInterval(refreshStats, 10000);
</script>
</body>
</html>
"""

def _parse_utc(value):
    """Convert an ISO timestamp/string to a timezone-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _db_connect():
    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=5,
        row_factory=dict_row,
    )


def init_db():
    """Create only Beautyrocket dashboard tables; never modify webhook tables."""
    if not DB_ENABLED:
        return

    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {QUEUE_TABLE} (
                    queue_id TEXT PRIMARY KEY,
                    created_at_utc TIMESTAMPTZ,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    post_id TEXT,
                    permalink TEXT,
                    buyer_score INTEGER,
                    buyer_band TEXT,
                    follower_score INTEGER,
                    follower_band TEXT,
                    best_score INTEGER,
                    target_action TEXT,
                    account_type TEXT,
                    categories JSONB,
                    safety_pass BOOLEAN,
                    suggested_comment TEXT,
                    caption TEXT,
                    writer_source TEXT,
                    completed_at_utc TIMESTAMPTZ,
                    raw_data JSONB NOT NULL DEFAULT '{{}}'::jsonb
                )
            """)

            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_pending_score
                ON {QUEUE_TABLE} (status, best_score DESC, buyer_score DESC, follower_score DESC)
            """)

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {STATS_TABLE} (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    total_likes BIGINT NOT NULL DEFAULT 0,
                    total_comment_suggestions BIGINT NOT NULL DEFAULT 0,
                    total_runs BIGINT NOT NULL DEFAULT 0,
                    last_run_likes INTEGER NOT NULL DEFAULT 0,
                    last_run_comment_suggestions INTEGER NOT NULL DEFAULT 0,
                    last_run_at_utc TIMESTAMPTZ,
                    last_run_new_queue_items INTEGER NOT NULL DEFAULT 0
                )
            """)

            cur.execute(f"""
                INSERT INTO {STATS_TABLE} (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING
            """)

        conn.commit()


def _queue_row_to_item(row):
    item = dict(row)
    raw_data = item.pop("raw_data", None) or {}

    # Preserve the dashboard's existing field names and any future fields.
    if isinstance(raw_data, dict):
        merged = {**raw_data, **item}
    else:
        merged = item

    if merged.get("categories") is None:
        merged["categories"] = []

    return merged


def load_queue():
    if DB_ENABLED:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT queue_id, created_at_utc, status, post_id, permalink,
                           buyer_score, buyer_band, follower_score, follower_band,
                           best_score, target_action, account_type, categories,
                           safety_pass, suggested_comment, caption, writer_source,
                           completed_at_utc, raw_data
                    FROM {QUEUE_TABLE}
                    ORDER BY created_at_utc DESC NULLS LAST
                """)
                return [_queue_row_to_item(row) for row in cur.fetchall()]

    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def upsert_queue_item(item):
    """Insert/update one opportunity with permanent post-level dedup.

    Returns True only when this request creates a genuinely new unique post
    opportunity. Existing PENDING/DONE/SKIPPED/POSTED rows block new queue IDs
    for the same Instagram post.
    """
    if not DB_ENABLED:
        return False

    queue_id = str(item.get("queue_id", "")).strip()
    post_id = str(item.get("post_id", "")).strip()
    if not queue_id:
        raise ValueError("Queue item is missing queue_id.")
    if not post_id:
        raise ValueError("Queue item is missing post_id.")

    categories = item.get("categories")
    if not isinstance(categories, list):
        categories = []

    raw_data = dict(item)
    created_at = _parse_utc(item.get("created_at_utc"))
    completed_at = _parse_utc(item.get("completed_at_utc"))

    with _db_connect() as conn:
        with conn.cursor() as cur:
            # Post-level guard. If ANY prior opportunity for this post is
            # PENDING/DONE/SKIPPED/POSTED, a new queue ID cannot resurrect it.
            cur.execute(f"""
                SELECT queue_id, status
                FROM {QUEUE_TABLE}
                WHERE post_id = %s
                  AND status IN ('PENDING', 'DONE', 'SKIPPED', 'POSTED')
                ORDER BY CASE status
                    WHEN 'DONE' THEN 1
                    WHEN 'SKIPPED' THEN 2
                    WHEN 'POSTED' THEN 3
                    WHEN 'PENDING' THEN 4
                    ELSE 5
                END, created_at_utc ASC NULLS LAST
                LIMIT 1
            """, (post_id,))
            existing = cur.fetchone()

            if existing and str(existing["queue_id"]) != queue_id:
                conn.commit()
                return False

            cur.execute(f"""
                INSERT INTO {QUEUE_TABLE} (
                    queue_id, created_at_utc, status, post_id, permalink,
                    buyer_score, buyer_band, follower_score, follower_band,
                    best_score, target_action, account_type, categories,
                    safety_pass, suggested_comment, caption, writer_source,
                    completed_at_utc, raw_data
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (queue_id) DO UPDATE SET
                    created_at_utc = EXCLUDED.created_at_utc,
                    status = CASE
                        WHEN {QUEUE_TABLE}.status IN ('DONE', 'SKIPPED')
                            THEN {QUEUE_TABLE}.status
                        ELSE EXCLUDED.status
                    END,
                    post_id = EXCLUDED.post_id,
                    permalink = EXCLUDED.permalink,
                    buyer_score = EXCLUDED.buyer_score,
                    buyer_band = EXCLUDED.buyer_band,
                    follower_score = EXCLUDED.follower_score,
                    follower_band = EXCLUDED.follower_band,
                    best_score = EXCLUDED.best_score,
                    target_action = EXCLUDED.target_action,
                    account_type = EXCLUDED.account_type,
                    categories = EXCLUDED.categories,
                    safety_pass = EXCLUDED.safety_pass,
                    suggested_comment = EXCLUDED.suggested_comment,
                    caption = EXCLUDED.caption,
                    writer_source = EXCLUDED.writer_source,
                    completed_at_utc = EXCLUDED.completed_at_utc,
                    raw_data = EXCLUDED.raw_data
            """, (
                queue_id, created_at, item.get("status", "PENDING"), post_id,
                item.get("permalink"), item.get("buyer_score"),
                item.get("buyer_band"), item.get("follower_score"),
                item.get("follower_band"), item.get("best_score"),
                item.get("target_action") or item.get("action_decision"),
                item.get("account_type"), Jsonb(categories), item.get("safety_pass"),
                item.get("suggested_comment"), item.get("caption"),
                item.get("writer_source"), completed_at, Jsonb(raw_data),
            ))
        conn.commit()
    return existing is None

def save_queue(data):
    """JSON-compatible local save; cloud status changes use direct SQL."""
    if DB_ENABLED:
        for item in data:
            upsert_queue_item(item)
        return

    tmp = QUEUE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, QUEUE_FILE)


def load_stats():
    default = {
        "total_likes": 0,
        "total_comment_suggestions": 0,
        "total_runs": 0,
        "last_run_likes": 0,
        "last_run_comment_suggestions": 0,
        "last_run_at_utc": None,
        "last_run_new_queue_items": 0,
        "pending_count_unique": 0,
        "pending_ai_comments": 0,
    }

    if DB_ENABLED:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT total_likes, total_comment_suggestions, total_runs,
                           last_run_likes, last_run_comment_suggestions,
                           last_run_at_utc, last_run_new_queue_items
                    FROM {STATS_TABLE}
                    WHERE id = 1
                """)
                row = cur.fetchone()

        if not row:
            return default

        data = dict(row)
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT
                        COUNT(DISTINCT NULLIF(post_id, '')) AS unique_posts,
                        COUNT(DISTINCT NULLIF(post_id, '')) FILTER (WHERE status = 'PENDING') AS pending_unique_posts,
                        COUNT(DISTINCT NULLIF(post_id, '')) FILTER (WHERE status = 'PENDING' AND COALESCE(suggested_comment, '') <> '') AS pending_ai_comments
                    FROM {QUEUE_TABLE}
                """)
                counts = dict(cur.fetchone() or {})
        # Dashboard opportunity totals are derived from unique Instagram posts,
        # so historical duplicate queue rows cannot inflate the visible stats.
        data["total_comment_suggestions"] = int(counts.get("unique_posts", 0) or 0)
        data["pending_count_unique"] = int(counts.get("pending_unique_posts", 0) or 0)
        data["pending_ai_comments"] = int(counts.get("pending_ai_comments", 0) or 0)
        data["last_run_comment_suggestions"] = int(data.get("last_run_comment_suggestions", 0) or 0)
        data["last_run_new_queue_items"] = int(data.get("last_run_new_queue_items", 0) or 0)
        if data.get("last_run_at_utc"):
            data["last_run_at_utc"] = data["last_run_at_utc"].isoformat()
        return {**default, **data}

    if not STATS_FILE.exists():
        return default
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        return {**default, **data} if isinstance(data, dict) else default
    except (OSError, ValueError, TypeError):
        return default


def save_stats(data):
    """Persist production statistics to PostgreSQL or the local JSON fallback."""
    if DB_ENABLED:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    UPDATE {STATS_TABLE}
                    SET total_likes = %s,
                        total_comment_suggestions = %s,
                        total_runs = %s,
                        last_run_likes = %s,
                        last_run_comment_suggestions = %s,
                        last_run_at_utc = %s,
                        last_run_new_queue_items = %s
                    WHERE id = 1
                """, (
                    data.get("total_likes", 0),
                    data.get("total_comment_suggestions", 0),
                    data.get("total_runs", 0),
                    data.get("last_run_likes", 0),
                    data.get("last_run_comment_suggestions", 0),
                    _parse_utc(data.get("last_run_at_utc")),
                    data.get("last_run_new_queue_items", 0),
                ))
            conn.commit()
        return

    tmp = STATS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATS_FILE)


def get_pending():
    if DB_ENABLED:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT q.queue_id, q.created_at_utc, q.status, q.post_id, q.permalink,
                           q.buyer_score, q.buyer_band, q.follower_score, q.follower_band,
                           q.best_score, q.target_action, q.account_type, q.categories,
                           q.safety_pass, q.suggested_comment, q.caption, q.writer_source,
                           q.completed_at_utc, q.raw_data
                    FROM {QUEUE_TABLE} q
                    WHERE q.status = 'PENDING'
                      AND q.post_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM {QUEUE_TABLE} blocked
                          WHERE blocked.post_id = q.post_id
                            AND blocked.status IN ('DONE', 'SKIPPED', 'POSTED')
                      )
                      AND q.queue_id = (
                          SELECT q2.queue_id FROM {QUEUE_TABLE} q2
                          WHERE q2.post_id = q.post_id
                            AND q2.status = 'PENDING'
                          ORDER BY q2.best_score DESC NULLS LAST, q2.created_at_utc ASC NULLS LAST
                          LIMIT 1
                      )
                    ORDER BY q.best_score DESC NULLS LAST,
                             q.buyer_score DESC NULLS LAST,
                             q.follower_score DESC NULLS LAST
                """)
                return [_queue_row_to_item(row) for row in cur.fetchall()]

    queue = load_queue()
    blocked = {str(x.get("post_id", "")).strip() for x in queue
               if str(x.get("post_id", "")).strip()
               and x.get("status") in {"DONE", "SKIPPED", "POSTED"}}
    pending = []
    seen = set()
    for item in queue:
        post_id = str(item.get("post_id", "")).strip()
        if item.get("status") != "PENDING" or not post_id or post_id in blocked or post_id in seen:
            continue
        seen.add(post_id)
        pending.append(item)
    pending.sort(
        key=lambda x: (
            x.get("best_score", 0),
            x.get("buyer_score", 0),
            x.get("follower_score", 0),
        ),
        reverse=True,
    )
    return pending


def set_queue_status(queue_id, status):
    """Persist DONE/SKIPPED without rewriting the entire queue."""
    completed_at = datetime.now(timezone.utc)

    if DB_ENABLED:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    UPDATE {QUEUE_TABLE} q
                    SET status = %s,
                        completed_at_utc = %s
                    WHERE q.post_id = (
                        SELECT post_id FROM {QUEUE_TABLE} WHERE queue_id = %s
                    )
                      AND q.status = 'PENDING'
                """, (status, completed_at, queue_id))
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    queue = load_queue()
    changed = False
    for item in queue:
        if item.get("queue_id") == queue_id and item.get("status") == "PENDING":
            item["status"] = status
            item["completed_at_utc"] = completed_at.isoformat()
            changed = True
            break
    if changed:
        save_queue(queue)
    return changed


# Initialize only our two dashboard tables when DATABASE_URL is configured.
# This does not drop, truncate, or modify any existing webhook tables.
init_db()

@app.route("/login", methods=["GET", "POST"])
def login():
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(32)

    if request.method == "POST":
        if not valid_csrf(request.form.get("csrf_token")):
            return ("Invalid request.", 400)

        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if secrets.compare_digest(username, WEB_USERNAME) and check_password_hash(WEB_PASSWORD_HASH, password):
            session.clear()
            session["authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            next_url = request.args.get("next") or url_for("index")
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)

        return render_template_string(
            LOGIN_HTML,
            error="Invalid username or password.",
            csrf_token=session["csrf_token"],
        ), 401

    return render_template_string(
        LOGIN_HTML,
        error=None,
        csrf_token=session["csrf_token"],
    )


@app.post("/logout")
@login_required
def logout():
    if not valid_csrf(request.form.get("csrf_token")):
        return ("Invalid request.", 400)
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    pending = get_pending()
    stats = load_stats()
    return render_template_string(
        HTML,
        pending=pending,
        stats=stats,
        csrf_token=session["csrf_token"],
    )


@app.get("/stats")
@login_required
def stats():
    data = load_stats()
    data["pending_count"] = len(get_pending())
    return jsonify(data)



@app.post("/api/ingest")
def api_ingest():
    """
    Receive queue opportunities and production stats from the local Windows bot.

    Authentication:
        X-Beautyrocket-Bot-Key: <BOT_API_KEY>

    The endpoint is intentionally one-way: it writes bot data into the
    dashboard database and does not execute Instagram actions.
    """
    if not BOT_API_KEY:
        return jsonify({"ok": False, "error": "bot_api_not_configured"}), 503

    supplied_key = request.headers.get("X-Beautyrocket-Bot-Key", "")
    if not supplied_key or not hmac.compare_digest(supplied_key, BOT_API_KEY):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if not DB_ENABLED:
        return jsonify({"ok": False, "error": "database_not_configured"}), 503

    if not request.is_json:
        return jsonify({"ok": False, "error": "application/json_required"}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "json_object_required"}), 400

    queue_items = payload.get("queue_items", [])
    stats = payload.get("stats")

    if queue_items is None:
        queue_items = []
    if not isinstance(queue_items, list):
        return jsonify({"ok": False, "error": "queue_items_must_be_list"}), 400

    if stats is not None and not isinstance(stats, dict):
        return jsonify({"ok": False, "error": "stats_must_be_object"}), 400

    # Keep the endpoint bounded so an accidental/malicious oversized request
    # cannot flood the dashboard database.
    if len(queue_items) > 100:
        return jsonify({"ok": False, "error": "too_many_queue_items"}), 413

    accepted = 0
    blocked = 0
    for item in queue_items:
        if (
            not isinstance(item, dict)
            or not str(item.get("queue_id", "")).strip()
            or not str(item.get("post_id", "")).strip()
        ):
            return jsonify({"ok": False, "error": "invalid_queue_item"}), 400

        # Post-level dedup is authoritative at the cloud boundary. A retry or
        # a different AI comment for an already-known post is not a new card.
        if upsert_queue_item(item):
            accepted += 1
        else:
            blocked += 1

    if stats is not None:
        # Only persist the known production-stat fields. This prevents
        # arbitrary JSON keys from becoming part of the stats record.
        allowed = {
            "total_likes",
            "total_comment_suggestions",
            "total_runs",
            "last_run_likes",
            "last_run_comment_suggestions",
            "last_run_at_utc",
            "last_run_new_queue_items",
        }
        clean_stats = {
            key: stats[key]
            for key in allowed
            if key in stats
        }
        # Never trust the local bot's duplicate-sensitive suggestion counters.
        # The cloud DB is the source of truth for unique-post statistics.
        clean_stats["last_run_new_queue_items"] = accepted
        clean_stats["last_run_comment_suggestions"] = accepted
        save_stats(clean_stats)

    return jsonify({
        "ok": True,
        "queue_items_accepted": accepted,
        "queue_items_blocked_as_duplicate": blocked,
        "stats_saved": stats is not None,
    })


@app.get("/healthz")
def healthz():
    if not DB_ENABLED:
        return jsonify({"status": "ok", "database": "json-fallback"})

    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return jsonify({"status": "ok", "database": "postgresql"})
    except Exception:
        return jsonify({"status": "error", "database": "postgresql"}), 503


@app.get("/background.png")
def background():
    if not BACKGROUND_FILE.exists():
        return ("Background image not found. Copy beautyrocket_background.png next to app.py.", 404)
    return send_file(BACKGROUND_FILE, mimetype="image/png", max_age=3600)


@app.post("/done/<queue_id>")
@login_required
def done(queue_id):
    if not valid_csrf(request.form.get("csrf_token")):
        return ("Invalid request.", 400)

    set_queue_status(queue_id, "DONE")
    return redirect(url_for("index"))


@app.post("/skip/<queue_id>")
@login_required
def skip(queue_id):
    if not valid_csrf(request.form.get("csrf_token")):
        return ("Invalid request.", 400)

    set_queue_status(queue_id, "SKIPPED")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

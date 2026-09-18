import json
import os
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, redirect, render_template_string, request, jsonify, send_file, session, url_for
from functools import wraps
from werkzeug.security import check_password_hash
import secrets

QUEUE_FILE = Path(os.getenv("BEAUTYROCKET_COMMENT_QUEUE_FILE", "beautyrocket_comment_queue.json"))
STATS_FILE = Path(os.getenv("BEAUTYROCKET_STATS_FILE", "beautyrocket_production_stats.json"))
BACKGROUND_FILE = Path(os.getenv("BEAUTYROCKET_BACKGROUND_FILE", "beautyrocket_background.png"))

app = Flask(__name__)

# Cloud dashboard authentication.
# Credentials are supplied through Render environment variables.
# Never hard-code them in this source file.
app.secret_key = os.getenv("BEAUTYROCKET_SESSION_SECRET") or secrets.token_urlsafe(48)
WEB_USERNAME = os.getenv("BEAUTYROCKET_WEB_USERNAME", "")
WEB_PASSWORD_HASH = os.getenv("BEAUTYROCKET_WEB_PASSWORD_HASH", "")

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
        <div class="title">COMMENT REVIEW</div>
        <div class="tagline">DISCOVER · ENGAGE · GROW</div>
    </header>

    <section class="production" aria-label="Production statistics">
        <div class="stat">
            <div class="stat-number" id="total-likes">{{ stats.total_likes }}</div>
            <div class="stat-label">Likes Performed</div>
        </div>
        <div class="stat">
            <div class="stat-number" id="total-comments">{{ stats.total_comment_suggestions }}</div>
            <div class="stat-label">Suggestions Queued</div>
        </div>
        <div class="stat">
            <div class="stat-number" id="pending-count">{{ pending|length }}</div>
            <div class="stat-label">Pending Opportunities</div>
        </div>
    </section>

    <div class="run-line">
        Last execution: <strong id="last-run-likes">+{{ stats.last_run_likes }}</strong> likes ·
        <strong id="last-run-comments">+{{ stats.last_run_comment_suggestions }}</strong> suggestions
        {% if stats.last_run_at_utc %} · {{ stats.last_run_at_utc[:19].replace('T', ' ') }} UTC{% endif %}
    </div>

    <div class="queue-head">
        <h2>Comment Opportunities</h2>
        <span class="pending-pill">{{ pending|length }} pending</span>
    </div>

    {% if pending %}
        {% for item in pending %}
        <div class="card">
            <div class="score">{{ item.best_score }}/100</div>
            <div class="meta">
                Buyer: {{ item.buyer_score }} ({{ item.buyer_band }}) ·
                Follower: {{ item.follower_score }} ({{ item.follower_band }})
            </div>
            <div class="meta">Category: {{ item.categories|join(', ') }}</div>

            <div class="comment" id="comment-{{ item.queue_id }}">{{ item.suggested_comment }}</div>

            <div class="buttons">
                <button class="copy" onclick="copyComment('comment-{{ item.queue_id }}', this)">📋 COPY COMMENT</button>
                <a class="btn open" href="{{ item.permalink }}" target="_blank" rel="noopener">🎬 OPEN REEL</a>
                <form method="post" action="/done/{{ item.queue_id }}">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <button class="done" type="submit">✓ DONE — COMMENT HANDLED</button>
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
        <div class="empty">No pending comment opportunities right now.<strong>YOU'RE ALL CAUGHT UP!</strong></div>
    {% endif %}

    <div style="text-align:center; margin-top:18px; display:flex; gap:8px; justify-content:center; flex-wrap:wrap;">
        <button class="refresh" onclick="location.reload()">↻ REFRESH DASHBOARD</button>
        <form method="post" action="/logout" style="display:inline;">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button class="refresh" type="submit">LOG OUT</button>
        </form>
    </div>
    <div class="footer">BEAUTYROCKET SECURE CLOUD DASHBOARD</div>
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
        document.getElementById('total-likes').innerText = data.total_likes;
        document.getElementById('total-comments').innerText = data.total_comment_suggestions;
        document.getElementById('pending-count').innerText = data.pending_count;
        document.getElementById('last-run-likes').innerText = '+' + data.last_run_likes;
        document.getElementById('last-run-comments').innerText = '+' + data.last_run_comment_suggestions;
    } catch (e) { /* local dashboard may briefly be unavailable */ }
}
setInterval(refreshStats, 10000);
</script>
</body>
</html>
"""

def load_queue():
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def save_queue(data):
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
    }
    if not STATS_FILE.exists():
        return default
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        return {**default, **data} if isinstance(data, dict) else default
    except (OSError, ValueError, TypeError):
        return default


def get_pending():
    queue = load_queue()
    pending = [x for x in queue if x.get("status") == "PENDING"]
    pending.sort(key=lambda x: (x.get("best_score", 0), x.get("buyer_score", 0), x.get("follower_score", 0)), reverse=True)
    return pending


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

    queue = load_queue()
    changed = False
    for item in queue:
        if item.get("queue_id") == queue_id and item.get("status") == "PENDING":
            item["status"] = "DONE"
            item["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            changed = True
            break
    if changed:
        save_queue(queue)
    return redirect(url_for("index"))


@app.post("/skip/<queue_id>")
@login_required
def skip(queue_id):
    if not valid_csrf(request.form.get("csrf_token")):
        return ("Invalid request.", 400)

    queue = load_queue()
    changed = False
    for item in queue:
        if item.get("queue_id") == queue_id and item.get("status") == "PENDING":
            item["status"] = "SKIPPED"
            item["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            changed = True
            break
    if changed:
        save_queue(queue)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

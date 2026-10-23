import os
import re
import csv
import json
import time
import hashlib
import sys

# Windows console compatibility: discovery output may contain Unicode
# arrows/emojis, so force UTF-8 output instead of the default cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timezone
from collections import Counter, defaultdict

import requests
from dotenv import load_dotenv
from buyer_product_link import comment_has_verified_product_link

load_dotenv()

ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID")

# A.5 cloud sync: the Windows bot sends queue/statistics data to the
# authenticated Render dashboard. Instagram credentials are never sent.
CLOUD_DASHBOARD_URL = os.getenv(
    "BEAUTYROCKET_CLOUD_DASHBOARD_URL",
    "https://beautyrocket-dashboard.onrender.com",
).rstrip("/")
BOT_API_KEY = os.getenv("BEAUTYROCKET_BOT_API_KEY", "").strip()
CLOUD_SYNC_TIMEOUT = 20

GRAPH_API_VERSION = "v26.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# ============================================================
# BEAUTYROCKET V4.5
# TWO-AUDIENCE SCORING + AUTOMATIC LIKES + HUMAN COMMENT REVIEW + CLOUD SYNC
# ============================================================
#
# V4.5 uses controlled live engagement safeguards and cloud-synced production statistics.
#
# Decision model:
#   Buyer score    -> Buyer audience qualification
#   Follower score -> Follower audience qualification
#   Action engine  -> Like only (V4.5)
#   Comment writer -> OpenAI/local fallback for human review candidates
#   Comment gate   -> quality check only; NOT a third audience score
#
# Safety always overrides scores.
#
# Engagement history is post/action-level:
#   same account + NEW post = evaluate normally
#   same account + SAME post/action = duplicate-block
#
# Live likes remain available; automatic comment posting is disabled in V4.5.
# Discovery likes use the proven IG User /likes endpoint with the Page token.
# V4.5: comment candidates are review-only. The human opens the exact Reel,
# copies/edits the suggestion, and manually posts inside Instagram.
# ============================================================

DRY_RUN = False
# V4.6: discovery/review only. No Instagram Like or Comment API actions are attempted.
LIVE_ENGAGEMENT = False
LIVE_COMMENT_POSTING = False

# V4.3.7 controlled-live safety: when enabled, only one Like may be sent
# during the first live validation run. Keep both flags False until instructed.
CONTROLLED_LIVE_TEST = False
CONTROLLED_LIVE_MAX_LIKES = 1
CONTROLLED_LIVE_MAX_COMMENTS = 1
# Controlled comment test is version-scoped so an older failed test cannot
# consume the one-attempt slot for this new validation run.
CONTROLLED_COMMENT_TEST_ID = "V4.5_DISABLED"

# Discovery
POST_LIMIT_PRIMARY = 25
POST_LIMIT_FALLBACK = 10
REQUEST_TIMEOUT = 30

CATEGORY_HASHTAGS = [
    "haircare",
    "hairproducts",
    "hairgrowth",
    "healthyhair",
    "hairproblems",
    "hairtips",
    "curlyhair",
    "naturalhair",
    "hairextensions",
    "hairtutorial",
    "beautyreview",
    "beautyproducts",
    "skincarehelp",
    "beautytips",
]

# Moderate live-phase limits are defined now, but are NOT activated
# while DRY_RUN is True.
MAX_LIKES_PER_DAY = 60
MAX_COMMENTS_PER_DAY = 12
MAX_ACTIONS_PER_RUN = 25
MIN_SECONDS_BETWEEN_ACTIONS = 45
MAX_CONSECUTIVE_API_ERRORS = 3

HISTORY_FILE = "beautyrocket_engagement_history.json"
COMMENT_EVALUATION_LIMIT = 25
AUDIT_FILE = "beautyrocket_v43_audit.csv"
STATE_FILE = "beautyrocket_v43_state.json"
COMMENT_REVIEW_FILE = "beautyrocket_comment_review.csv"
COMMENT_HISTORY_FILE = "beautyrocket_comment_history.csv"
COMMENT_GENERATOR_VERSION = "V4.6"
COMMENT_REVIEW_LIMIT = 100

# V4.5: comments are suggestions only. They are never posted by Beautyrocket.
COMMENT_QUEUE_FILE = "beautyrocket_comment_queue.json"
COMMENT_QUEUE_MIN_SCORE = 75

# V4.5 production dashboard statistics. This persists across script runs.
PRODUCTION_STATS_FILE = "beautyrocket_production_stats.json"


# Hybrid AI comment engine. Beautyrocket remains the decision-maker; OpenAI
# is used only to write comments for already-qualified candidates.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
OPENAI_API_URL = "https://api.openai.com/v1/responses"
MAX_AI_COMMENT_CALLS_PER_RUN = 20
MAX_AI_COMMENT_CALLS_PER_DAY = 200
AI_COMMENT_MAX_OUTPUT_TOKENS = 110
AI_REQUEST_TIMEOUT = 30
AI_MIN_REVIEW_BAND = {"VERY_HIGH", "HIGH", "MEDIUM"}
AI_STATE_FILE = "beautyrocket_ai_state.json"
AI_COMMENT_LOG_FILE = "beautyrocket_ai_comment_log.csv"

# ============================================================
# CATEGORY WEIGHTS
# ============================================================

CATEGORY_WEIGHTS = {
    "beauty": 20,
    "hair": 24,
    "fashion": 18,
    "lifestyle": 15,
    "motherhood": 10,
    "womanhood": 8,
    "humor": 5,
}

# V4.3.27 audience-specific category signals. These are deliberately kept
# separate from CATEGORY_WEIGHTS so a buyer signal does not automatically
# inflate the follower score (or vice versa).
BUYER_CATEGORY_SIGNALS = {
    "beauty_problem_solution": 16,
}

FOLLOWER_CATEGORY_SIGNALS = {
    "personality_relatability": 14,
}

BEAUTY_PROBLEM_SOLUTION_PHRASES = [
    # Explicit problem language
    "frizz", "frizzy", "flyaways", "breakage", "split ends",
    "short lashes", "short-lash", "short lash", "sparse lashes", "thin lashes",
    "weak lashes", "brittle lashes", "lash length", "lash definition",
    "lashes could use", "lashes need", "need more length", "need more definition",
    "hair damage", "damaged hair", "dry hair", "dry scalp",
    "itchy scalp", "oily scalp", "hair loss", "thinning hair",
    "acne", "breakouts", "dark spots", "hyperpigmentation",
    "dry skin", "sensitive skin", "oily skin", "uneven skin",
    "texture", "fine lines", "puffiness", "dark circles",
    "hair problem", "hair problems", "skin problem", "skin problems",
    "hooded eyes", "makeup creasing", "makeup melting",
    "foundation matching", "color match", "what works for",
    "what worked for", "how do i fix", "how can i fix",
    "how do i get rid of", "how can i get rid of", "trying to fix",
    "struggling with", "struggle with", "need a solution",
    "need something for", "looking for something for", "looking for a product",
]

PERSONALITY_RELATABILITY_PHRASES = [
    "storytime", "story time", "pov", "hot take", "unpopular opinion",
    "rant", "let me vent", "can we talk about", "real talk",
    "be honest", "tell me i'm not the only one", "am i the only one",
    "who else", "anyone else", "this happened to me", "i can't be the only one",
    "you know when", "we all do it", "we've all", "we have all",
    "adulting", "plot twist", "chaos", "awkward", "embarrassing",
    "pet peeve", "pet peeves", "things that annoy me", "things that bother me",
    "tell me your", "tell me yours", "drop your", "drop yours",
    "what is your", "what's your", "your girl", "girl math",
    "my unpopular opinion", "my hot take", "just between us",
]

# ============================================================
# BUYER SIGNALS
# ============================================================

PURCHASE_INTENT = {
    "amazon": 12,
    "amazon finds": 25,
    "amazon find": 22,
    "amazon favorites": 25,
    "amazon favorite": 22,
    "amazon must haves": 25,
    "amazon must have": 22,
    "amazon essentials": 24,
    "amazon haul": 22,
    "amazon shopping": 20,
    "link in bio": 14,
    "shop link": 16,
    "shop my": 16,
    "where did you get": 20,
    "where can i get": 20,
    "where can i buy": 30,
    "where to buy": 28,
    "where can i purchase": 30,
    "where can i find": 20,
    "where is this from": 20,
    "what products": 16,
    "what product": 14,
    "what are you using": 18,
    "what do you use": 18,
    "what do you recommend": 20,
    "recommendations": 14,
    "favorite products": 20,
    "favorite product": 18,
    "must have": 16,
    "must haves": 18,
    "essentials": 12,
    "beauty essentials": 20,
    "hair essentials": 22,
    "wardrobe essentials": 20,
    "what i'm wearing": 18,
    "what im wearing": 18,
    "outfit details": 18,
    "what i'm using": 18,
    "what im using": 18,
    "currently using": 16,
    "hair products": 18,
    "hair product": 16,
    "beauty products": 18,
    "beauty product": 16,
    "beauty tools": 20,
    "hair tools": 20,
    "skincare products": 20,
}

PROBLEM_SOLUTION = {
    "how do i": 7,
    "how to": 8,
    "help with": 8,
    "looking for": 9,
    "need help": 8,
    "need recommendations": 14,
    "what should i": 10,
    "which one": 8,
    "which products": 15,
    "best products": 18,
    "best product": 16,
    "best hair": 12,
    "best beauty": 12,
    "best skincare": 15,
    "best makeup": 15,
    "best hairstyle": 12,
    "best outfit": 12,
    "trying to find": 12,
    "looking to buy": 18,
    "dry scalp": 10,
    "hair keeps breaking": 12,
    "hair breakage": 10,
    "want to try": 12,
    "i need this": 14,
}

ROUTINE_SIGNALS = {
    "beauty routine": 15,
    "hair routine": 18,
    "skincare routine": 17,
    "morning routine": 8,
    "night routine": 8,
    "self care": 8,
    "self-care": 8,
    "hair care": 16,
    "haircare": 16,
    "makeup routine": 15,
    "daily essentials": 14,
    "getting ready": 8,
    "grwm": 10,
    "glow up": 10,
    "glow-up": 10,
    "hack": 10,
    "hacks": 10,
    "tutorial": 8,
    "tutorials": 8,
}

PRODUCT_TERMS = {
    "hair dryer": 10,
    "blow dryer": 10,
    "flat iron": 10,
    "straightener": 10,
    "curling iron": 10,
    "curling wand": 10,
    "hair brush": 8,
    "detangling brush": 9,
    "hair mask": 10,
    "shampoo": 8,
    "conditioner": 8,
    "heat protectant": 10,
    "hair oil": 10,
    "hair growth oil": 15,
    "scalp oil": 12,
    "hair growth": 8,
    "edge control": 8,
    "bonnet": 8,
    "wig": 8,
    "extensions": 8,
    "makeup": 8,
    "foundation": 8,
    "concealer": 8,
    "mascara": 7,
    "lip gloss": 7,
    "skincare": 9,
    "moisturizer": 8,
    "serum": 8,
    "sunscreen": 8,
    "face mask": 8,
    "beauty tool": 10,
    "facial tool": 10,
    "jewelry": 7,
    "handbag": 9,
    "purse": 9,
    "shoes": 8,
    "dress": 8,
    "jeans": 7,
    "leggings": 7,
    "sunglasses": 7,
    "accessories": 7,
    "outfit": 8,
}

ASPIRATIONAL = {
    "elevated": 7,
    "luxury": 7,
    "luxurious": 7,
    "chic": 8,
    "stylish": 8,
    "effortless": 6,
    "glam": 7,
    "glamorous": 7,
    "feminine": 6,
    "femininity": 6,
    "confidence": 5,
    "treat yourself": 8,
    "feel good": 5,
    "look good": 6,
    "look better": 7,
    "style": 5,
}

# ============================================================
# ACCOUNT / BUSINESS CLASSIFICATION
# ============================================================

HARD_BUSINESS_PHRASES = [
    "wholesale",
    "wholesaler",
    "manufacturer",
    "oem",
    "odm",
    "dropshipping",
    "drop shipping",
    "bulk order",
    "bulk orders",
    "become a vendor",
    "become a reseller",
    "distributor",
    "distributors",
    "book me",
    "book now",
    "book an appointment",
    "book appointment",
    "appointments available",
    "schedule an appointment",
    "click the link below to book",
    "services available",
    "our services",
    "service provider",
    "licensed esthetician",
    "esthetician",
    "med spa",
    "medical spa",
    "salon",
    "hair salon",
    "hair stylist",
    "hairstylist",
    "makeup artist",
    "nail tech",
    "real estate",
    "contact us",
    "contact me for pricing",
    "contact for pricing",
    "dm for pricing",
    "pricing available",
    "shop now",
    "buy now",
    "order now",
    "our collection",
    "our new collection",
    "new arrivals",
    "available now",
    "limited time",
    "free shipping",
    "promo code",
    "use code",
    "affiliate",
]

BUSINESS_HINTS = [
    "llc",
    "inc",
    "official",
    "store",
    "boutique",
    "shop",
    "brand",
    "studio",
    "agency",
    "company",
    "co.",
    "enterprise",
    "cosmetics",
    "skincare brand",
    "hair brand",
    "clothing brand",
    "retailer",
]

# ============================================================
# SENSITIVE / HIGH-RISK CONTEXT
# ============================================================

SENSITIVE_PHRASES = [
    "death",
    "died",
    "funeral",
    "rest in peace",
    "rip",
    "grief",
    "mourning",
    "suicide",
    "self harm",
    "self-harm",
    "abuse",
    "domestic violence",
    "assault",
    "rape",
    "trauma",
    "cancer",
    "terminal",
    "miscarriage",
    "stillbirth",
    "pregnancy loss",
    "hospital",
    "emergency",
    "tragedy",
    "tragedies",
    "disaster",
    "war",
    "shooting",
    "political",
    "politics",
    "religion",
    "election",
    "controversy",
    "controversial",
]

LOW_VALUE_PHRASES = [
    "follow me",
    "follow for follow",
    "like for like",
    "l4l",
    "f4f",
    "dm me",
    "dm for",
    "check out my page",
    "viral",
    "explore page",
]

# ============================================================
# TEXT HELPERS
# ============================================================

def normalize_text(text):
    if not text:
        return ""
    text = str(text).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def phrase_present(text, phrase):
    text = normalize_text(text)
    phrase = normalize_text(phrase)
    if not phrase:
        return False

    # Word-boundary matching for single words prevents
    # false positives such as "war" inside "warranty".
    if " " not in phrase and re.fullmatch(r"[a-z0-9-]+", phrase):
        return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text))

    return phrase in text


def score_dictionary(text, dictionary):
    total = 0
    matches = []

    for phrase, points in dictionary.items():
        if phrase_present(text, phrase):
            total += points
            matches.append({"phrase": phrase, "points": points})

    return total, matches


def detect_phrases(text, phrases):
    return [p for p in phrases if phrase_present(text, p)]


# ============================================================
# API
# ============================================================

def graph_get(endpoint, params=None):
    graph_get.last_error = None
    if not ACCESS_TOKEN:
        print("ERROR: INSTAGRAM_ACCESS_TOKEN is not configured.")
        return None

    params = dict(params or {})
    params["access_token"] = ACCESS_TOKEN

    try:
        response = requests.get(
            f"{BASE_URL}/{endpoint}",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        print(f"GET {endpoint.split('?')[0]} -> HTTP {response.status_code}")

        if response.ok:
            try:
                return response.json()
            except ValueError:
                print("API returned invalid JSON.")
                return None

        try:
            data = response.json()
            error = data.get("error", {})
            graph_get.last_error = {
                "status_code": response.status_code,
                "code": error.get("code"),
                "type": error.get("type"),
                "message": error.get("message", ""),
            }
            print(
                "API error: "
                f"{error.get('message', 'Unknown API error')} | "
                f"type={error.get('type', 'Unknown')} | "
                f"code={error.get('code', 'Unknown')}"
            )
        except ValueError:
            graph_get.last_error = {
                "status_code": response.status_code,
                "code": None,
                "type": None,
                "message": "Unparseable API error response",
            }
            print("API error response could not be parsed.")

        return None

    except requests.RequestException as exc:
        print(f"Network/API request failed: {type(exc).__name__}")
        return None



def graph_post(endpoint, params=None, token=None):
    """
    Safe POST wrapper for live engagement.

    Never prints request URLs or access tokens. Returns parsed JSON on success
    and stores safe error metadata on graph_post.last_error.
    """
    graph_post.last_error = None

    token = token or PAGE_ACCESS_TOKEN
    if not token:
        graph_post.last_error = {
            "status_code": None,
            "code": None,
            "type": "CONFIGURATION",
            "message": "Page access token is not configured.",
        }
        print("LIVE ACTION ERROR: Page access token is not configured.")
        return None

    request_params = dict(params or {})
    request_params["access_token"] = token

    try:
        response = requests.post(
            f"{BASE_URL}/{endpoint}",
            params=request_params,
            timeout=REQUEST_TIMEOUT,
        )

        print(f"POST {endpoint.split('?')[0]} -> HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError:
            graph_post.last_error = {
                "status_code": response.status_code,
                "code": None,
                "type": None,
                "message": "Unparseable API response",
            }
            print("API returned invalid JSON.")
            return None

        if response.ok:
            return data

        error = data.get("error", {})
        graph_post.last_error = {
            "status_code": response.status_code,
            "code": error.get("code"),
            "type": error.get("type"),
            "message": error.get("message", ""),
        }
        print(
            "API action error: "
            f"{error.get('message', 'Unknown API error')} | "
            f"type={error.get('type', 'Unknown')} | "
            f"code={error.get('code', 'Unknown')}"
        )
        return None

    except requests.RequestException as exc:
        graph_post.last_error = {
            "status_code": None,
            "code": None,
            "type": type(exc).__name__,
            "message": "Network/API request failed",
        }
        print(f"Network/API action failed: {type(exc).__name__}")
        return None


def like_media(post_id):
    """Like a discovered Instagram media object using the proven endpoint."""
    return graph_post(
        f"{INSTAGRAM_ACCOUNT_ID}/likes",
        {"media_id": str(post_id)},
    )


def reply_to_comment(comment_id, message):
    """Reply to an existing Instagram comment using the proven endpoint."""
    return graph_post(
        f"{comment_id}/replies",
        {"message": message},
    )


def create_top_level_comment(media_id, message):
    """Attempt to create a new top-level comment on an Instagram media object."""
    return graph_post(
        f"{media_id}/comments",
        {"message": message},
    )



def search_hashtag(hashtag):
    data = graph_get(
        "ig_hashtag_search",
        {
            "user_id": INSTAGRAM_ACCOUNT_ID,
            "q": hashtag,
        },
    )

    if not data:
        return None

    results = data.get("data", [])
    if not results:
        print(f"No hashtag ID found for #{hashtag}.")
        return None

    return results[0].get("id")


def enrich_media_owner(post):
    """No-op for hashtag-discovered media.

    Meta's hashtag recent_media edge intentionally does not expose the
    creator username/owner, and arbitrary hashtag media IDs cannot reliably
    be fetched as individual media objects with this API flow. Keep the
    function for compatibility, but do not make an extra Graph API request.
    """
    return post


def get_recent_hashtag_media(hashtag_id):
    if not hashtag_id:
        return []

    endpoint = f"{hashtag_id}/recent_media"

    # IMPORTANT: keep this field set limited to fields supported by the
    # hashtag recent_media edge. Owner identity is enriched separately.
    params = {
        "user_id": INSTAGRAM_ACCOUNT_ID,
        "fields": "id,caption,media_type,permalink,timestamp",
    }

    data = graph_get(endpoint, {**params, "limit": POST_LIMIT_PRIMARY})
    if data:
        return data.get("data", [])

    error = getattr(graph_get, "last_error", None) or {}
    message = str(error.get("message", "")).lower()
    is_payload_error = (
        error.get("status_code") == 500
        or error.get("code") == 1
        or "reduce the amount of data" in message
    )

    if not is_payload_error:
        return []

    print(
        f"Meta rejected {POST_LIMIT_PRIMARY} posts; "
        f"retrying with {POST_LIMIT_FALLBACK}."
    )
    fallback = graph_get(
        endpoint,
        {**params, "limit": POST_LIMIT_FALLBACK},
    )
    if fallback:
        return fallback.get("data", [])

    return []


# ============================================================
# CATEGORY DETECTION
# ============================================================

def detect_categories(caption, hashtags):
    text = normalize_text(f"{caption or ''} {' '.join(hashtags or [])}")
    categories = set()

    if any(phrase_present(text, p) for p in [
        "beauty", "makeup", "skincare", "skin care", "cosmetic",
        "glam", "foundation", "concealer", "mascara"
    ]):
        categories.add("beauty")

    if any(phrase_present(text, p) for p in [
        "hair", "haircare", "hair care", "hairstyle", "hairstyles",
        "wig", "weave", "extensions", "curl", "braid", "braids"
    ]):
        categories.add("hair")

    if any(phrase_present(text, p) for p in [
        "fashion", "outfit", "ootd", "dress", "shoes", "jeans",
        "handbag", "purse", "jewelry", "wardrobe", "style"
    ]):
        categories.add("fashion")

    if any(phrase_present(text, p) for p in [
        "lifestyle", "routine", "self care", "self-care", "home",
        "daily life", "day in my life", "grwm"
    ]):
        categories.add("lifestyle")

    if any(phrase_present(text, p) for p in [
        "motherhood", "momlife", "mom life", "mama", "mom",
        "mother", "kids", "children", "raising kids"
    ]):
        categories.add("motherhood")

    if any(phrase_present(text, p) for p in [
        "womanhood", "women", "woman", "feminine",
        "femininity", "girls"
    ]):
        categories.add("womanhood")

    if any(phrase_present(text, p) for p in [
        "funny", "humor", "humour", "comedy", "lol",
        "laugh", "ðŸ˜‚", "ðŸ¤£"
    ]):
        categories.add("humor")

    # V4.3.27: buyer-focused category for people actively describing a
    # beauty/hair/skin/style problem or looking for a practical solution.
    if any(phrase_present(text, p) for p in BEAUTY_PROBLEM_SOLUTION_PHRASES):
        categories.add("beauty_problem_solution")

    # V4.3.27: follower-focused category for personality-led, relatable,
    # storytime, opinion, rant, and everyday-human content.
    if any(phrase_present(text, p) for p in PERSONALITY_RELATABILITY_PHRASES):
        categories.add("personality_relatability")

    for hashtag in hashtags or []:
        tag = normalize_text(hashtag)
        if tag in {"beauty", "beautytips", "beautyproducts"}:
            categories.add("beauty")
        elif tag in {"haircare", "hairproducts", "hairgrowth", "healthyhair", "curlyhair", "naturalhair", "hairextensions", "hairtutorial"}:
            categories.add("hair")
        elif tag in {"fashion", "womensfashion"}:
            categories.add("fashion")
        elif tag == "lifestyle":
            categories.add("lifestyle")
        elif tag in {"motherhood", "momlife"}:
            categories.add("motherhood")
        elif tag in {"women", "womanhood"}:
            categories.add("womanhood")
        elif tag in {"funnywomen", "womenshumor"}:
            categories.add("humor")
        elif tag in {"hairproblems", "hairproblem", "hairtips", "beautytips", "skincareproblems", "skincarehelp", "beautyreview"}:
            categories.add("beauty_problem_solution")
        elif tag in {"storytime", "storytimes", "hottake", "hottakes", "unpopularopinion", "relatable", "relatablewomen", "pov"}:
            categories.add("personality_relatability")

    return sorted(categories)


def category_score(categories):
    scores = sorted(
        (CATEGORY_WEIGHTS.get(c, 0) for c in categories),
        reverse=True,
    )

    if not scores:
        return 0

    total = scores[0]
    if len(scores) >= 2:
        total += int(scores[1] * 0.35)
    if len(scores) >= 3:
        total += int(scores[2] * 0.15)

    return min(total, 38)


# ============================================================
# ACCOUNT CLASSIFICATION
# ============================================================

def classify_account(caption, username=None, account_metadata=None):
    metadata = normalize_text(json.dumps(account_metadata or {}, ensure_ascii=False))
    caption_text = normalize_text(caption)
    username_text = normalize_text(username)
    combined = f"{username_text} {metadata} {caption_text}"

    hard_matches = detect_phrases(combined, HARD_BUSINESS_PHRASES)
    business_matches = detect_phrases(combined, BUSINESS_HINTS)

    if hard_matches:
        phrase = hard_matches[0]
        if any(
            p in phrase
            for p in [
                "wholesale", "manufacturer", "oem", "odm",
                "dropshipping", "drop shipping", "distributor"
            ]
        ):
            account_type = "MANUFACTURER"
        elif any(
            p in phrase
            for p in [
                "book", "appointment", "esthetician", "med spa",
                "salon", "stylist", "makeup artist", "nail tech",
                "services", "service provider"
            ]
        ):
            account_type = "SERVICE_PROVIDER"
        else:
            account_type = "BUSINESS"
        return account_type, hard_matches, business_matches

    if len(business_matches) >= 2:
        return "BUSINESS", hard_matches, business_matches

    # Conservative creator/personal inference.
    personal_signals = [
        "i ", "i'm", "im ", "my ", "me ", "we ", "mom ",
        "mama ", "my life", "storytime", "get ready with me",
        "grwm", "day in my life",
    ]
    creator_signals = [
        "creator", "content creator", "influencer", "blogger",
        "beauty creator", "fashion creator", "lifestyle creator",
    ]

    if any(phrase_present(caption_text, p) for p in creator_signals):
        return "CREATOR", hard_matches, business_matches

    if sum(1 for p in personal_signals if phrase_present(caption_text, p)) >= 1:
        return "PERSONAL", hard_matches, business_matches

    return "UNKNOWN", hard_matches, business_matches


# ============================================================
# FOLLOWER OPPORTUNITY
# ============================================================

def calculate_follower_opportunity(
    caption,
    categories,
    account_type,
    business_matches,
    history_for_account,
):
    text = normalize_text(caption)

    score = 0
    matches = []

    # V4.3.27: personality/relatability is a follower-only signal.
    if "personality_relatability" in categories:
        bonus = FOLLOWER_CATEGORY_SIGNALS["personality_relatability"]
        score += bonus
        matches.append(f"personality-relatability+{bonus}")

    # Personal/creator identity is the core follower signal.
    if account_type == "PERSONAL":
        score += 32
        matches.append("personal-account-signal")
    elif account_type == "CREATOR":
        score += 40
        matches.append("creator-signal")
    elif account_type == "INFLUENCER":
        score += 42
        matches.append("influencer-signal")
    elif account_type == "UNKNOWN":
        score += 12
        matches.append("unknown-conservative-base")

    # Audience overlap.
    overlap = len(
        set(categories)
        & {"beauty", "hair", "fashion", "lifestyle", "motherhood", "womanhood", "humor"}
    )
    score += min(overlap * 7, 28)

    if overlap:
        matches.append(f"{overlap}-audience-overlap")

    # Strong multi-category relevance is more valuable than a single hashtag match.
    # This rewards personal accounts whose content consistently overlaps Sheila's audience.
    if overlap >= 4:
        score += 6
        matches.append("strong-audience-overlap")
    elif overlap >= 3:
        score += 3
        matches.append("good-audience-overlap")

    # Substantive personal posts are stronger follower opportunities than short captions.
    # Keep the bonus modest so caption length alone cannot create a high score.
    word_count = len(text.split())
    if word_count >= 30:
        score += 6
        matches.append("substantive-caption")
    elif word_count >= 15:
        score += 3
        matches.append("developed-caption")

    personal_context_phrases = [
        "my life", "my day", "real life", "as a mom", "as a mother",
        "as a wife", "my family", "my kids", "my children", "relatable",
        "mom life", "motherhood", "girlhood", "womanhood"
    ]
    context_hits = [
        p for p in personal_context_phrases if phrase_present(text, p)
    ]
    if context_hits:
        score += min(4, len(context_hits) * 2)
        matches.append("personal-context")

    # Conversation/personal context.
    conversation_phrases = [
        "what do you think",
        "tell me",
        "let me know",
        "who else",
        "anyone else",
        "can you relate",
        "have you",
        "do you",
        "would you",
        "which one",
        "what do you use",
        "what do you recommend",
        "my favorite",
        "my story",
        "my experience",
        "i love",
        "i hate",
        "i can't",
        "i am",
        "i'm",
        "we are",
    ]

    conversation_hits = [
        p for p in conversation_phrases if phrase_present(text, p)
    ]

    if conversation_hits:
        score += min(18, len(conversation_hits) * 6)
        matches.extend(conversation_hits[:3])

    # Consistent relevant activity from the same account is positive.
    prior_quality = history_for_account.get("positive_actions", 0)
    prior_posts = history_for_account.get("distinct_posts", 0)

    if prior_quality >= 1:
        bonus = min(prior_quality * 4, 12)
        score += bonus
        matches.append(f"positive-history+{bonus}")

    if prior_posts >= 2:
        score += 5
        matches.append("repeat-relevant-account")

    # Business evidence is a hard negative.
    if account_type in {
        "BUSINESS",
        "BRAND",
        "RETAILER",
        "MANUFACTURER",
        "SERVICE_PROVIDER",
    }:
        return 0, "DO_NOT_ENGAGE", ["business-hard-exclusion"]

    if business_matches:
        score = min(score, 20)
        matches.append("business-hint")

    score = max(0, min(score, 100))

    if score >= 80:
        label = "VERY_HIGH"
    elif score >= 65:
        label = "HIGH"
    elif score >= 50:
        label = "MEDIUM"
    elif score >= 30:
        label = "LOW"
    else:
        label = "IGNORE"

    return score, label, matches


# ============================================================
# COMMENT QUALITY
# ============================================================

def extract_comment_subject(categories, product_matches, text):
    if "hair" in categories and product_matches:
        return "hair"
    if "beauty" in categories and any(
        phrase_present(text, p)
        for p in ["skincare", "makeup", "foundation", "serum", "mascara", "lip", "skin"]
    ):
        return "skincare"
    if "fashion" in categories:
        return "style"
    if "motherhood" in categories:
        return "motherhood"
    if "humor" in categories:
        return "humor"
    if "womanhood" in categories:
        return "womanhood"
    return "content"


# Historical outputs from V1-V4.3.9 showed repeated generic comments.
# These are used as a memory bank so new comments are rejected when they are
# exact or near-duplicates of comments already produced in earlier runs.
HISTORICAL_COMMENT_MEMORY = [
    "Love this beauty content! âœ¨",
    "Love this look! âœ¨",
    "Love this hair! âœ¨",
    "Love this! ðŸ’•",
    "Love this beauty moment! âœ¨",
    "Such a relatable motherhood moment! ðŸ’•",
    "This is such a relatable mom moment ðŸ˜‚ðŸ’•",
    "So relatable â€” motherhood really does have moments like this ðŸ˜‚ðŸ’•",
    "This one got me ðŸ˜‚ðŸ’• So relatable!",
    "This is such an easy post to connect with âœ¨ðŸ’•",
    "Okay, this is the kind of mom moment so many of us get ðŸ˜‚ðŸ’•",
]

GENERIC_COMMENT_PATTERNS = [
    "love this",
    "love this look",
    "love this hair",
    "love this beauty content",
    "so relatable",
    "relatable moment",
    "this made me laugh",
    "this one got me",
    "such a cute way",
    "i like how",
    "i like that",
    "the part about",
    "the part",
    "the detail",
    "the way you",
    "the piece",
    "stood out to me",
    "caught my attention",
    "genuinely useful",
    "nicely said",
]

COMMENT_STOPWORDS = {
    "this", "that", "with", "from", "have", "your", "you", "are", "and",
    "the", "for", "was", "but", "just", "like", "into", "about", "when",
    "what", "who", "how", "why", "they", "them", "their", "its", "it's",
    "really", "such", "very", "more", "than", "then", "here", "there",
    "because", "would", "could", "should", "been", "being", "were", "will",
    "not", "our", "out", "all", "one", "too", "also", "still", "only",
    "can", "get", "got", "make", "made", "has", "had", "does", "did", "i",
    "me", "my", "we", "us", "it", "of", "to", "in", "on", "a", "an",
    "honestly", "okay", "listen", "kinda", "kind", "type", "part", "line",
    "detail", "reference", "thing", "things", "someone", "something",
    "actually", "literally", "probably", "definitely", "way", "first",
    "everyone", "people", "women", "woman", "girls",
}

# Words that are too weak to be the centerpiece of a comment.
WEAK_DETAIL_WORDS = COMMENT_STOPWORDS | {
    "today", "tomorrow", "yesterday", "everyone", "someone", "something",
    "stuff", "content", "post", "reel", "instagram", "caption", "video",
    "moment", "feeling", "feelings", "thought", "thoughts",
}

# High-value concrete subjects. The generator prefers these over arbitrary
# caption words so comments sound intentional rather than assembled.
DETAIL_GROUPS = {
    "hair": {
        "layers", "curls", "curl", "braids", "braid", "color", "texture",
        "length", "volume", "waves", "wave", "routine", "products", "hair",
        "highlights", "blonde", "brunette", "bangs", "ponytail", "extensions",
        "wig", "weave",
    },
    "beauty": {
        "makeup", "foundation", "concealer", "mascara", "lipstick", "lip",
        "gloss", "brows", "eyeliner", "skincare", "serum", "spf",
        "sunscreen", "moisturizer", "skin", "glow", "lashes",
    },
    "style": {
        "dress", "denim", "jeans", "boots", "shoes", "bag", "purse", "top",
        "jacket", "outfit", "colors", "layers", "style", "sweater",
        "heels", "sneakers", "skirt", "blazer", "accessories", "jewelry",
    },
    "motherhood": {
        "mother", "mom", "moms", "kids", "children", "school", "dinner",
        "routine", "chaos", "laundry", "bedtime", "toddlers", "toddler",
        "baby", "babies", "husband", "wife", "parenting", "snacks",
        "homework", "carpool", "nap",
    },
    "humor": {
        "mother", "mom", "moms", "kids", "children", "husband", "dating",
        "work", "chaos", "nap", "routine", "overthink", "marriage",
        "husband", "wife", "toddlers", "school",
    },
    "womanhood": {
        "confidence", "identity", "style", "self-expression", "work", "life",
        "beauty", "motherhood", "relationships", "dating", "career",
        "boundaries", "aging", "girlhood",
    },
}

def comment_tokens(comment):
    return {
        token for token in re.findall(r"[a-z0-9']+", normalize_text(comment))
        if token not in COMMENT_STOPWORDS and len(token) >= 3
    }


def comment_similarity(a, b):
    ta = comment_tokens(a)
    tb = comment_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def comment_is_too_similar(comment, existing_comments):
    normalized = normalize_text(comment)
    for old in existing_comments:
        old_normalized = normalize_text(old)
        if normalized == old_normalized:
            return True
        if comment_similarity(comment, old) >= 0.60:
            return True
    return False


def caption_detail_words(text):
    """Extract concrete, descriptive caption terms; never use filler as a detail."""
    words = re.findall(r"[a-z][a-z'-]{3,}", normalize_text(text))
    seen = set()
    useful = []
    for word in words:
        if word in WEAK_DETAIL_WORDS or word in seen:
            continue
        if word in {
            "follow", "save", "share", "comment", "reel", "instagram", "link",
            "today", "people", "everyone", "someone", "something", "things",
            "because", "really", "actually", "literally", "probably",
        }:
            continue
        seen.add(word)
        useful.append(word)
    return useful


def _clean_product_detail(product_matches):
    for match in product_matches or []:
        if isinstance(match, dict):
            phrase = normalize_text(match.get("phrase", "")).strip()
        else:
            phrase = normalize_text(str(match)).strip()
        if (
            phrase
            and phrase not in WEAK_DETAIL_WORDS
            and len(phrase) >= 4
            and "{" not in phrase
            and "}" not in phrase
        ):
            return phrase
    return None


def comment_subject_detail(subject, text, product_matches):
    """Select the strongest concrete subject rather than a generic caption word."""
    product_detail = _clean_product_detail(product_matches)
    if product_detail:
        return product_detail

    details = caption_detail_words(text)
    preferred = DETAIL_GROUPS.get(subject, set())

    # Prefer exact high-value subject words.
    for item in details:
        if item in preferred:
            return item

    # Then use a subject-aware descriptive word if it is genuinely specific.
    for item in details:
        if len(item) >= 5 and item not in WEAK_DETAIL_WORDS:
            return item

    return None


def comment_has_clean_detail(comment, detail):
    cleaned = normalize_text(detail).strip()
    if not cleaned or cleaned in WEAK_DETAIL_WORDS:
        return False
    if len(cleaned) < 4:
        return False
    if "{" in comment or "}" in comment or "'points'" in comment:
        return False
    return True


def load_comment_history_memory():
    comments = []
    if not os.path.exists(COMMENT_HISTORY_FILE):
        return comments
    try:
        with open(COMMENT_HISTORY_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                comment = (row.get("generated_comment") or "").strip()
                if comment:
                    comments.append(comment)
    except (OSError, csv.Error):
        return comments
    return comments


def _comment_is_safe_for_tone(comment):
    """Keep edgy personality playful; never turn it into harassment or hostility."""
    lower = normalize_text(comment)
    forbidden = [
        "ugly", "stupid", "idiot", "fat", "gross", "disgusting",
        "hate you", "kill", "die", "loser",
    ]
    return not any(phrase_present(lower, word) for word in forbidden)


def _comment_options(subject, detail, text):
    """Personality-forward templates. Every option contains a concrete reference."""
    detail_display = detail

    options = {
        "skincare": [
            f"Okay, the {detail_display} tip is actually worth stealing ðŸ‘€âœ¨",
            f"Not me mentally adding that {detail_display} move to my routine ðŸ˜‚",
            f"The {detail_display} recommendation? Yep, you got my attention ðŸ‘€",
            f"That {detail_display} tip is the kind of beauty shortcut I want saved immediately ðŸ˜‚âœ¨",
            f"Okayyy, the {detail_display} is doing more work than it has any business doing ðŸ˜‚",
        ],
        "hair": [
            f"Okay, those {detail_display} are doing the heavy lifting here ðŸ”¥",
            f"The {detail_display} came to play. No notes ðŸ˜‚ðŸ”¥",
            f"Not me zooming in on the {detail_display} like I don't have somewhere to be ðŸ‘€",
            f"That {detail_display} is exactly why this look works. Whew ðŸ”¥",
            f"Yeahhh, the {detail_display} understood the assignment ðŸ˜‚âœ¨",
            f"I wasn't prepared for that {detail_display} to look THAT good ðŸ‘€",
        ],
        "style": [
            f"Okay, the {detail_display} just made this whole outfit make sense ðŸ”¥",
            f"Not the {detail_display} casually stealing the whole show ðŸ˜‚",
            f"That {detail_display} is a dangerous level of good ðŸ‘€ðŸ”¥",
            f"The {detail_display} choice? Bold. And somehow exactly right ðŸ˜‚âœ¨",
            f"Yeah, I'm looking at the {detail_display} and rethinking my own closet ðŸ˜‚",
            f"That {detail_display} really changed the whole energy of the look ðŸ”¥",
        ],
        "motherhood": [
            f"The {detail_display} situation is painfully accurate ðŸ˜‚",
            f"Okay, the {detail_display} part of motherhood deserves its own support group ðŸ˜‚",
            f"Not the {detail_display} chaos being this relatable ðŸ˜‚ðŸ’•",
            f"The {detail_display} struggle really said, 'good luck, girl' ðŸ˜‚",
            f"I felt that {detail_display} one in my soul ðŸ˜‚ðŸ’•",
            f"Yep. The {detail_display} is where motherhood stops being a hobby ðŸ˜‚",
        ],
        "humor": [
            f"Okay, the {detail_display} got me ðŸ˜‚",
            f"Not the {detail_display} catching strays in this ðŸ˜‚",
            f"The {detail_display} line was WILD ðŸ˜‚ I was not ready.",
            f"Yeah, the {detail_display} is exactly where I lost it ðŸ˜‚",
            f"The {detail_display} reference took me OUT ðŸ˜‚",
            f"That {detail_display} joke was dangerously accurate ðŸ˜‚",
        ],
        "womanhood": [
            f"The {detail_display} point hit a little too close to home ðŸ‘€ðŸ’•",
            f"Okay, the {detail_display} conversation is one more women need to have ðŸ‘",
            f"That {detail_display} observation is painfully real ðŸ‘€",
            f"The {detail_display} angle made this way more interesting than the usual advice âœ¨",
            f"Yeah, the {detail_display} part is something I think a LOT of women get ðŸ‘€",
            f"That take on {detail_display} actually made me stop scrolling ðŸ‘€âœ¨",
        ],
        "content": [
            f"Okay, the {detail_display} is what made me stop scrolling ðŸ‘€",
            f"That {detail_display} caught me completely off guard ðŸ˜‚",
            f"The {detail_display} angle here is actually so good ðŸ‘€âœ¨",
            f"Not me immediately having an opinion about the {detail_display} ðŸ˜‚",
            f"Yeah, the {detail_display} is the part I would've remembered from this one ðŸ‘€",
        ],
    }
    return options.get(subject, options["content"])


def _clean_comment_detail(detail):
    """Return a safe, concrete phrase suitable for a human-sounding comment."""
    if not detail:
        return None

    detail = normalize_text(str(detail)).strip(" .,!?:;-'\"")
    if not detail:
        return None

    low = detail.lower()
    banned = {
        "the", "this", "that", "these", "those", "it", "its", "they",
        "said", "saying", "apparently", "everything", "something",
        "someone", "everyone", "another", "personal", "calm", "looking",
        "trying", "raising", "finish", "stayed", "falta", "excuse",
        "reference", "angle", "conversation", "point", "piece", "part",
        "detail", "line", "joke", "chaos",
    }

    # Never allow scoring metadata or internal serialization into comments.
    if any(marker in detail for marker in ("{", "}", "'points'", '"points"')):
        return None

    # Reject pronouns, isolated function words, and fragments that are
    # grammatically unsafe when inserted into a sentence.
    if low in banned or len(low.split()) > 5:
        return None
    if low.startswith(("the ", "a ", "an ", "i ", "you ", "your ", "we ", "they ")):
        return None

    return detail


def _caption_has(text, *phrases):
    low = normalize_text(text).lower()
    return any(p in low for p in phrases)


def _semantic_comment_options(subject, text):
    """Build comments from the meaning/signals of the caption, not random words."""
    low = normalize_text(text).lower()
    options = []

    # Humor/motherhood: respond to an actual scenario or punchline.
    if _caption_has(low, "invent a game", "made up 4 seconds ago", "doing it wrong"):
        options.extend([
            "Kids really do create the rules AND somehow become the referee ðŸ˜‚",
            "The confidence of a child making up rules on the spot is unmatched ðŸ˜‚",
            "Not the tiny CEO enforcing rules they invented five minutes ago ðŸ˜‚",
        ])

    if _caption_has(low, "wednesday", "monday took", "tuesday came back", "friday feels"):
        options.extend([
            "Wednesday really has no business acting this far away from Friday ðŸ˜‚",
            "Friday being treated like a long-distance relationship is painfully accurate ðŸ˜‚",
            "Monday and Tuesday absolutely did not need to team up like that ðŸ˜‚",
        ])

    if _caption_has(low, "pick me", "if the shoe fits"):
        options.extend([
            "If having your own opinions is a crime, apparently we're all in trouble ðŸ˜‚",
            "The internet really does hand out the 'pick me' label like candy ðŸ˜‚",
            "Honestly, the confidence to just own it is what makes this funny ðŸ˜‚",
        ])

    if _caption_has(low, "very calm", "people just keep testing", "anger management"):
        options.extend([
            "The 'I'm calm until noon' plot twist is taking me OUT ðŸ˜‚",
            "Coffee, optimism, and then humanity ruins the schedule ðŸ˜‚",
            "That escalated from peaceful morning to anger-management allegations REAL fast ðŸ˜‚",
        ])

    if _caption_has(low, "pray about it", "immediately started worrying"):
        options.extend([
            "The 'I'll pray about it' followed by immediate overthinking is too real ðŸ˜‚",
            "Nothing like saying 'I'll pray about it' and then opening a full investigation in your head ðŸ˜‚",
        ])

    # Beauty/hair/style: use explicit items only when the caption actually names them.
    if _caption_has(low, "pixie"):
        options.extend([
            "A good pixie cut can make somebody look like they just upgraded their whole personality ðŸ”¥",
            "That pixie cut is giving 'new chapter, no announcement necessary' âœ¨",
        ])

    if _caption_has(low, "floral", "two-piece", "2-piece", "off-shoulder", "trousers"):
        options.extend([
            "That floral two-piece is doing a lot of work without looking overdone ðŸ”¥",
            "The off-shoulder with wide-leg trousers combo is a whole vibe ðŸ‘€",
        ])

    if _caption_has(low, "makeup", "mascara", "foundation", "brows", "serum", "skincare"):
        options.extend([
            "Okayyy, now THAT is the kind of beauty tip that makes me want to try it ðŸ˜‚âœ¨",
            "A beauty shortcut that actually feels doable? I'm listening ðŸ‘€",
        ])

    if _caption_has(low, "curls", "waves", "braids", "layers", "hair color", "haircare"):
        options.extend([
            "Those curls are absolutely refusing to have a bad day ðŸ”¥",
            "Okay, those waves are making a very strong case for trying this look ðŸ‘€",
            "The hair transformation is giving 'walk out feeling brand new' energy âœ¨",
        ])

    # Motherhood/life: use concrete family situations named in the caption.
    if _caption_has(low, "bathroom in peace"):
        options.extend([
            "Getting five uninterrupted minutes in the bathroom really is luxury motherhood ðŸ˜‚",
            "The fact that bathroom privacy becomes a five-star experience after kids ðŸ˜‚",
        ])

    if _caption_has(low, "school", "school drop"):
        options.extend([
            "The school-day scramble deserves its own Olympic event ðŸ˜‚",
            "School mornings really know how to turn coffee into a survival tool ðŸ˜‚",
        ])

    if _caption_has(low, "laundry"):
        options.extend([
            "Laundry is somehow the chore that respawns the second you finish it ðŸ˜‚",
            "The laundry pile really believes it pays rent around here ðŸ˜‚",
        ])

    if _caption_has(low, "bedtime"):
        options.extend([
            "Bedtime with kids is basically a negotiation where nobody follows the agenda ðŸ˜‚",
            "Why does bedtime take three business days when everyone is already tired ðŸ˜‚",
        ])

    if _caption_has(low, "birthday"):
        options.extend([
            "Birthdays have a way of turning one normal day into a full production ðŸ˜‚",
            "A birthday somehow creates twelve new things to remember at once ðŸ˜‚",
        ])

    # Fallbacks are based on explicit semantic category, never arbitrary caption words.
    if not options:
        if subject == "style":
            options.extend([
                "Okayyy, this look has enough personality to make me rethink my own closet ðŸ‘€",
                "That styling choice is bold without trying too hard ðŸ”¥",
            ])
        elif subject == "hair":
            options.extend([
                "This hair look has the kind of finish that makes you want to copy it immediately ðŸ‘€",
                "Okayyy, this is making a strong case for switching up the routine ðŸ”¥",
            ])
        elif subject == "skincare":
            options.extend([
                "Okayyy, this is the kind of beauty routine I actually want to hear more about ðŸ‘€",
                "That is a beauty tip I can see people genuinely trying âœ¨",
            ])
        elif subject == "motherhood":
            options.extend([
                "Motherhood really does turn ordinary moments into full stories ðŸ˜‚ðŸ’•",
                "The mom-life plot twists really do write themselves ðŸ˜‚ðŸ’•",
            ])
        elif subject == "humor":
            options.extend([
                "Okayyy, that is the kind of joke that sneaks up on you ðŸ˜‚",
                "I was not prepared for that punchline ðŸ˜‚",
            ])
        elif subject == "womanhood":
            options.extend([
                "Okayyy, THAT is a conversation women can actually have some opinions about ðŸ‘€",
                "There is definitely a whole conversation hiding in that one ðŸ‘€",
            ])
        else:
            # Do not manufacture a reaction when the caption gives us no
            # concrete subject. A believable no-comment decision is better
            # than a generic engagement-bait sentence.
            pass

    return options


def _extract_natural_comment_detail(subject, text, product_matches):
    """Return only a concrete subject when it is explicitly supported by the caption."""
    low = normalize_text(text).lower()

    explicit_terms = {
        "hair": [
            "curls", "waves", "braids", "layers", "hair color", "pixie cut",
            "pixie", "hair", "edges", "volume",
        ],
        "skincare": [
            "serum", "brows", "makeup", "mascara", "foundation", "sunscreen",
            "skin", "skincare", "hydration",
        ],
        "style": [
            "denim", "jeans", "boots", "dress", "jacket", "bag", "purse",
            "shoes", "outfit", "floral", "trousers",
        ],
        "motherhood": [
            "toddlers", "kids", "children", "baby", "bedtime", "school",
            "laundry", "birthday", "bathroom", "husband", "mom", "motherhood",
        ],
        "humor": [
            "dating", "husband", "kids", "toddlers", "work", "chaos",
            "bathroom", "bedtime", "wednesday", "friday", "monday", "tuesday",
        ],
        "womanhood": [
            "confidence", "relationships", "motherhood", "career", "beauty",
            "style", "self-expression", "women", "womanhood",
        ],
    }

    # Product matches are accepted only if they are clean and visibly present.
    for match in product_matches or []:
        value = match.get("phrase", "") if isinstance(match, dict) else match
        cleaned = _clean_comment_detail(value)
        if cleaned and cleaned.lower() in low:
            return cleaned

    for term in explicit_terms.get(subject, []):
        if re.search(r"\b" + re.escape(term) + r"\b", low):
            cleaned = _clean_comment_detail(term)
            if cleaned:
                return cleaned

    # Do not fall back to caption_detail_words. Arbitrary tokens caused the
    # "the apparently joke", "the everything", etc. failures in V4.3.18.
    return None


def _comment_caption_signals(subject, text):
    signals = set()
    low = normalize_text(text).lower()

    if any(p in low for p in [
        "ðŸ˜‚", "ðŸ¤£", "lol", "lmao", "wild", "crazy", "chaos",
        "not me", "i can't", "i cant", "send help", "too much",
    ]):
        signals.add("funny")

    if any(p in low for p in [
        "what do you think", "which one", "who else", "anyone else",
        "tell me", "let me know", "would you", "have you", "do you",
        "agree", "am i the only one",
    ]):
        signals.add("conversation")

    return signals


def _comment_variants(subject, detail, signals, text=""):
    """Return semantic comments with concrete detail when available.

    V4.3.22 deliberately prefers NO COMMENT over generic filler. Comments
    should react to an actual subject, scenario, joke, or question in the
    source caption.
    """
    options = _semantic_comment_options(subject, text)

    if detail:
        if subject == "hair":
            options.extend([
                f"Okayyy, those {detail} are doing some serious work ðŸ”¥",
                f"Not me immediately understanding why those {detail} work so well ðŸ‘€",
                f"Those {detail} really changed the whole energy of this look ðŸ”¥",
            ])
        elif subject == "style":
            options.extend([
                f"That {detail} choice is making a very strong case for itself ðŸ‘€ðŸ”¥",
                f"Okayyy, that {detail} is doing exactly what it needed to do ðŸ”¥",
                f"That {detail} choice is bold without making the whole look feel like it's trying too hard ðŸ‘€",
            ])
        elif subject == "skincare":
            options.extend([
                f"Okayyy, that {detail} tip actually has my attention ðŸ‘€",
                f"A {detail} tip that sounds simple enough to actually stick with? I'm listening ðŸ‘€",
            ])
        elif subject == "motherhood":
            options.extend([
                f"{detail.capitalize()} really does turn into a whole event ðŸ˜‚ðŸ’•",
                f"Why does {detail} somehow become a full production every single time ðŸ˜‚",
            ])
        elif subject == "humor":
            options.extend([
                f"The {detail} situation is exactly where this went off the rails ðŸ˜‚",
                f"Okay, the {detail} situation took this from normal to ridiculous real quick ðŸ˜‚",
            ])
        elif subject == "womanhood":
            options.extend([
                f"The {detail} conversation is exactly why women need to be able to say the quiet part out loud ðŸ‘€",
            ])
        elif subject == "humor":
            options.extend([
                f"The {detail} situation really had no business being that accurate ðŸ˜‚",
                f"Okay, the {detail} situation is painfully accurate ðŸ˜‚",
            ])

    return options

def generate_unique_review_comment(post, existing_comments=None):
    """Generate a specific, conversational, personality-forward review comment.

    V4.3.23:
      - prioritizes semantic caption scenarios over isolated words
      - never uses arbitrary caption tokens as comment subjects
      - avoids filler such as "the part", "the detail", "the line"
      - uses humor/personality when the source caption supports it
      - never claims an action or experience the bot did not perform
      - rejects near-duplicates using persistent comment history
    """
    existing_comments = list(existing_comments or [])
    existing_comments.extend(HISTORICAL_COMMENT_MEMORY)

    text = normalize_text(post.get("caption", ""))
    categories = post.get("categories", [])
    product_matches = post.get("product_matches", [])
    subject = extract_comment_subject(categories, product_matches, text)

    detail = _extract_natural_comment_detail(subject, text, product_matches)
    signals = _comment_caption_signals(subject, text)
    variants = _comment_variants(subject, detail, signals, text)

    if not variants:
        return None

    digest = hashlib.sha256(
        f"{post.get('id','')}|{text}|{COMMENT_GENERATOR_VERSION}".encode("utf-8")
    ).hexdigest()
    start = int(digest[:8], 16) % len(variants)
    ordered = variants[start:] + variants[:start]

    forbidden_phrases = [
        "the part", "the piece", "the detail", "the line",
        "i like how", "i love how", "i really like how",
        "this is so relatable", "love this", "so relatable",
        "stood out to me", "the reference", "the angle",
        "the conversation", "the point", "the joke",
        "the everything", "the said", "the apparently",
        "the personal", "the calm", "the exited",
        "deserves way more honesty", "beautiful kind of confidence",
        "such a beautiful kind", "kind of confidence",
        "there's something so", "there is something so",
        "proof you can", "proof that you can",
    ]

    for candidate in ordered:
        normalized = normalize_text(candidate)

        if any(pattern in normalized for pattern in forbidden_phrases):
            continue

        if comment_is_too_similar(candidate, existing_comments):
            continue

        # Reject comments containing known malformed constructions.
        if re.search(r"\bthe\s+\w+\s+(?:are|is|was|were)\b", normalized):
            # Allow normal noun phrases such as "the laundry pile is..."
            allowed = ("the laundry pile is", "the off-shoulder with")
            if not any(phrase in normalized for phrase in allowed):
                continue

        # A comment generated from an explicit detail must retain that detail.
        # Semantic scenario comments may intentionally use a full phrase instead.
        if detail and detail.lower() not in normalized:
            semantic = candidate in _semantic_comment_options(subject, text)
            if not semantic:
                continue

        return candidate

    return None


# ============================================================
# BUYER OPPORTUNITY
# ============================================================

def calculate_buyer_opportunity(caption, hashtags):
    text = normalize_text(f"{caption or ''} {' '.join(hashtags or [])}")

    categories = detect_categories(caption, hashtags)
    sensitive_matches = detect_phrases(text, SENSITIVE_PHRASES)
    low_value_matches = detect_phrases(text, LOW_VALUE_PHRASES)

    c_score = category_score(categories)
    purchase_score, purchase_matches = score_dictionary(text, PURCHASE_INTENT)
    problem_score, problem_matches = score_dictionary(text, PROBLEM_SOLUTION)
    routine_score, routine_matches = score_dictionary(text, ROUTINE_SIGNALS)
    aspirational_score, aspirational_matches = score_dictionary(text, ASPIRATIONAL)
    product_score, product_matches = score_dictionary(text, PRODUCT_TERMS)

    purchase_score = min(purchase_score, 45)
    problem_score = min(problem_score, 25)
    routine_score = min(routine_score, 8)
    aspirational_score = min(aspirational_score, 5)
    product_score = min(product_score, 25)

    buyer_category_score = 0
    buyer_category_matches = []
    if "beauty_problem_solution" in categories:
        buyer_category_score = BUYER_CATEGORY_SIGNALS["beauty_problem_solution"]
        buyer_category_matches.append("beauty-problem-solution")

    # Reduce broad category influence for buyer qualification only.
    # Shared category weights and follower scoring remain unchanged.
    buyer_category_relevance_score = int(c_score * 0.5)

    score = (
        buyer_category_relevance_score
        + purchase_score
        + problem_score
        + routine_score
        + aspirational_score
        + product_score
        + buyer_category_score
    )

    if low_value_matches:
        score -= min(len(low_value_matches) * 5, 15)

    shopping_signal_count = (
        len(purchase_matches)
        + len(product_matches)
        + len(problem_matches)
    )
    has_explicit_intent = bool(purchase_matches or problem_matches)

    if has_explicit_intent and shopping_signal_count >= 2:
        score += 8
    if has_explicit_intent and shopping_signal_count >= 4:
        score += 5

    if sensitive_matches:
        score = min(score, 15)

    score = max(0, min(score, 100))

    if sensitive_matches:
        label = "DO_NOT_ENGAGE"
    elif score >= 78 and (
        purchase_score >= 10
        or product_score >= 10
        or problem_score >= 10
    ):
        label = "VERY_HIGH"
    elif score >= 62 and (
        purchase_score >= 8
        or product_score >= 8
        or problem_score >= 8
    ):
        label = "HIGH"
    elif score >= 48:
        label = "MEDIUM"
    elif score >= 30:
        label = "LOW"
    else:
        label = "IGNORE"

    return {
        "buyer_score": score,
        "buyer_intent": label,
        "categories": categories,
        "category_score": c_score,
        "purchase_intent": purchase_score,
        "problem_score": problem_score,
        "routine_score": routine_score,
        "aspirational_score": aspirational_score,
        "product_score": product_score,
        "buyer_category_score": buyer_category_score,
        "buyer_category_matches": buyer_category_matches,
        "sensitive_matches": sensitive_matches,
        "low_value_matches": low_value_matches,
        "purchase_matches": purchase_matches,
        "product_matches": product_matches,
        "problem_matches": problem_matches,
    }


# ============================================================
# SAFETY + DECISIONS
# ============================================================

def safety_gate(post):
    if post["sensitive_matches"]:
        return False, "SENSITIVE_CONTENT"

    if post["account_type"] in {
        "BUSINESS",
        "BRAND",
        "RETAILER",
        "MANUFACTURER",
        "SERVICE_PROVIDER",
    }:
        return False, "BUSINESS_OR_COMPETITOR"

    if post.get("duplicate_post"):
        return False, "DUPLICATE_POST"

    if post.get("api_anomaly"):
        return False, "API_ANOMALY"

    return True, "PASS"


def buyer_action_decision(score):
    if score >= 90:
        return "LIKE_CANDIDATE"
    if score >= 75:
        return "LIKE_CANDIDATE"
    return "NO_BUYER_ACTION"


def follower_action_decision(post):
    score = post["follower_score"]

    # Primary path: strong follower opportunity.
    if score >= 75:
        return "LIKE_CANDIDATE"

    # Secondary qualification path: a clearly personal/creator account with
    # strong audience overlap and substantive personal context.
    account_type = post.get("account_type")
    eligible_types = {"PERSONAL", "CREATOR", "INFLUENCER"}
    categories = set(post.get("categories", []))
    overlap = len(
        categories
        & {"beauty", "hair", "fashion", "lifestyle", "motherhood", "womanhood", "humor"}
    )
    matches = set(post.get("follower_matches", []))
    has_substantive = bool(
        {"substantive-caption", "developed-caption"} & matches
    )
    has_personal_context = "personal-context" in matches

    if (
        score >= 55
        and account_type in eligible_types
        and overlap >= 3
        and has_substantive
        and has_personal_context
    ):
        return "LIKE_CANDIDATE"

    return "NO_FOLLOWER_ACTION"


def comment_review_band(post):
    """Return the highest non-IGNORE audience score band for comment research."""
    bands = []
    for value in (post.get("buyer_intent"), post.get("follower_opportunity")):
        if value in {"VERY_HIGH", "HIGH", "MEDIUM", "LOW"}:
            bands.append(value)
    order = {"VERY_HIGH": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    return max(bands, key=lambda x: order[x]) if bands else None


def load_ai_action_state():
    default = {
        "date": utc_day_key(),
        "calls": 0,
        "errors": 0,
        "last_error": None,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }

    if not os.path.exists(AI_STATE_FILE):
        return default

    try:
        with open(AI_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") != utc_day_key():
            return default
        return {**default, **state}
    except (OSError, ValueError):
        return default


def save_ai_action_state(state):
    tmp = AI_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, AI_STATE_FILE)


def ai_comment_allowed(state):
    if not OPENAI_API_KEY:
        return False, "OPENAI_API_KEY_NOT_CONFIGURED"
    if state.get("calls", 0) >= MAX_AI_COMMENT_CALLS_PER_DAY:
        return False, "AI_DAILY_LIMIT"
    return True, "PASS"


def clean_ai_comment(text):
    """Normalize model output into one reviewable Instagram comment."""
    if not text:
        return None

    comment = str(text).strip()
    comment = re.sub(r"^```(?:text)?\s*", "", comment, flags=re.IGNORECASE)
    comment = re.sub(r"\s*```$", "", comment).strip()
    comment = comment.strip('"\' ')

    if not comment:
        return None

    # We want a single natural comment, not an explanation or numbered list.
    if "\n" in comment or re.match(r"^(?:1[.)]|[-*])\s", comment):
        return None
    if len(comment) > 240:
        return None
    if not _comment_is_safe_for_tone(comment):
        return None

    normalized = normalize_text(comment)
    forbidden = [
        "as an ai", "i can't view", "i cannot view", "great post",
        "love this", "so relatable", "nice post", "thanks for sharing",
        "check out my", "follow me", "dm me", "link in bio",
    ]
    if any(pattern in normalized for pattern in forbidden):
        return None

    return comment


def validate_ai_comment(comment, post, existing_comments):
    if not comment:
        return False, "EMPTY"

    normalized = normalize_text(comment)
    if comment_is_too_similar(comment, existing_comments):
        return False, "DUPLICATE_OR_TOO_SIMILAR"

    if not _comment_is_safe_for_tone(comment):
        return False, "TONE_SAFETY"

    # V4.3.23: reject polished/greeting-card language even when grammatically correct.
    formal_voice_phrases = [
        "meaningful reset", "so powerful", "profound reminder",
        "beautiful reminder", "what a reminder", "truly inspiring",
        "deeply inspiring", "so empowering", "empowering reminder",
        "inspiring journey", "beautiful journey", "meaningful journey",
        "such an inspiration", "what an inspiration",
        "beautiful kind of confidence", "beautiful kind of",
        "such a beautiful", "kind of confidence",
        "deserves way more honesty", "there's something so",
        "there is something so", "proof you can", "proof that you can",
    ]
    if any(phrase_present(normalized, phrase) for phrase in formal_voice_phrases):
        return False, "FORMAL_OR_GREETING_CARD_TONE"

    # Keep the model grounded in the actual caption. Reject comments that are
    # pure generic engagement bait even if they sound fluent.
    subject = extract_comment_subject(
        post.get("categories", []),
        post.get("product_matches", []),
        normalize_text(post.get("caption", "")),
    )
    caption = normalize_text(post.get("caption", ""))
    signals = _comment_caption_signals(subject, caption)
    concrete_terms = set()
    for term in DETAIL_GROUPS.get(subject, set()):
        if phrase_present(caption, term):
            concrete_terms.add(term)

    # For highly specific captions, require at least one grounded signal.
    if concrete_terms and not any(term in normalized for term in concrete_terms):
        semantic_options = _semantic_comment_options(subject, caption)
        if not any(normalize_text(opt) in normalized for opt in semantic_options):
            # The model may paraphrase a concrete term, so don't reject solely
            # on exact-word mismatch when the caption has a clear scenario.
            if not signals and len(caption.split()) >= 25:
                return False, "NOT_GROUNDED_ENOUGH"

    words = comment.split()
    if len(words) < 6:
        return False, "TOO_SHORT"
    if len(words) > 26:
        return False, "TOO_LONG"

    # Reject outputs that look cut off. A casual comment may omit punctuation,
    # but an AI response ending on a plain alphanumeric character is especially
    # likely to have been truncated mid-thought. Emojis and normal punctuation
    # are accepted as natural endings.
    last_char = comment.rstrip()[-1:]
    if last_char and last_char.isalnum():
        return False, "INCOMPLETE_THOUGHT"

    trailing_fragments = {
        "a", "an", "and", "are", "as", "at", "because", "but", "by", "for",
        "from", "if", "in", "into", "is", "it", "my", "of", "on", "or",
        "that", "the", "their", "there", "this", "to", "was", "were",
        "with", "your",
    }
    final_word = re.sub(r"[^a-zA-Z']", "", words[-1]).lower()
    if final_word in trailing_fragments:
        return False, "INCOMPLETE_THOUGHT"

    return True, "PASS"


def openai_generate_comment(post, state, existing_comments):
    """Ask OpenAI for one grounded comment. Never decides whether to engage."""
    allowed, reason = ai_comment_allowed(state)
    if not allowed:
        return None, reason

    caption = str(post.get("caption", "")).strip()
    if not caption:
        return None, "NO_CAPTION"

    categories = ", ".join(post.get("categories", [])) or "general lifestyle"
    target = post.get("target_decision", "BUYER_OR_FOLLOWER")
    subject = extract_comment_subject(
        post.get("categories", []),
        post.get("product_matches", []),
        caption,
    )

    system_prompt = (
        "You write Instagram comments for a beauty, hair, fashion, lifestyle, "
        "motherhood, and women's-humor creator. Beautyrocket has already decided "
        "that this post is eligible for engagement. You are ONLY the comment writer; "
        "never decide whether to engage. Write one short, natural, personality-forward "
        "comment that reacts to something actually present in the caption. Sound like "
        "a real woman casually commenting on Instagramâ€”not a copywriter, brand, "
        "therapist, coach, or greeting card. Emotional comments are welcome when the post is "
        "personal or heartfelt: be warm, sincere, and human, but use everyday conversational "
        "language instead of polished, poetic, inspirational, or formal phrasing. Avoid greeting-card language such as meaningful reset, so powerful, profound reminder, inspiring journey, beautiful kind of confidence, or proof you can. Do not turn an ordinary caption into a motivational statement. For humor, "
        "be playful, witty, and a little edgy when it fits. For beauty, hair, or fashion, be "
        "enthusiastic and specific. Match the emotional weight of the post instead of forcing "
        "jokes. Never be hostile. Do not advertise, sell, ask people to follow, mention AI, "
        "or invent facts. Do not pretend you personally used a product or experienced the "
        "situation. Avoid generic phrases like 'love this', 'so relatable', 'great post', "
        "'nice post', or vague praise that could fit any caption. React to one specific detail, "
        "joke, feeling, situation, or idea from the caption. Use contractions, casual wording, "
        "and natural Instagram phrasing. A little personality is better than perfect prose. "
        "Do not explain your choice. Return ONLY one complete comment, 8-24 words long. "
        "Use 6-26 words only if needed for a natural complete thought. It MUST be fully "
        "finished and end naturally with punctuation or an emoji. Never stop mid-sentence, "
        "mid-phrase, or on a dangling word. Emojis are allowed when they genuinely fit."
    )

    user_prompt = (
        f"Target context: {target}\n"
        f"Subject: {subject}\n"
        f"Categories: {categories}\n"
        "Caption:\n"
        f"{caption[:1800]}\n\n"
        "Write exactly one complete comment, ideally 8-24 words. Keep it natural and "
        "conversational, like something a real woman would type without overthinking it. "
        "If the post is emotional, be emotionally sincere and warm without becoming poetic, formal, or motivational. "
        "formal. If the post is funny, be playful or a little edgy when it fits. If the post "
        "is beauty, hair, or fashion, react to a concrete thing shown or mentioned. Do not make "
        "it sound inspirational, corporate, polished, or AI-generated. Finish the thought "
        "completely and do not end on a dangling phrase. Use emojis only when they genuinely fit."
    )

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_output_tokens": AI_COMMENT_MAX_OUTPUT_TOKENS,
    }

    state["calls"] = state.get("calls", 0) + 1

    try:
        response = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=payload,
            timeout=AI_REQUEST_TIMEOUT,
        )

        if not response.ok:
            state["errors"] = state.get("errors", 0) + 1
            try:
                error_data = response.json()
                message = error_data.get("error", {}).get("message", "OpenAI API error")
            except ValueError:
                message = "OpenAI API returned an unreadable error."
            state["last_error"] = f"HTTP {response.status_code}: {message[:300]}"
            print(f"AI COMMENT ERROR: HTTP {response.status_code} | {message[:300]}")
            return None, "OPENAI_API_ERROR"

        data = response.json()
        usage = data.get("usage", {}) or {}
        state["total_input_tokens"] = state.get("total_input_tokens", 0) + int(
            usage.get("input_tokens", 0) or 0
        )
        state["total_output_tokens"] = state.get("total_output_tokens", 0) + int(
            usage.get("output_tokens", 0) or 0
        )

        output_text = data.get("output_text")
        if not output_text:
            pieces = []
            for item in data.get("output", []) or []:
                for content in item.get("content", []) or []:
                    if content.get("type") == "output_text":
                        pieces.append(content.get("text", ""))
            output_text = " ".join(pieces).strip()

        comment = clean_ai_comment(output_text)
        if not comment:
            return None, "AI_OUTPUT_INVALID"

        valid, validation_reason = validate_ai_comment(
            comment,
            post,
            existing_comments,
        )
        if not valid:
            return None, f"AI_VALIDATION_{validation_reason}"

        return comment, "AI_SUCCESS"

    except requests.RequestException as exc:
        state["errors"] = state.get("errors", 0) + 1
        state["last_error"] = f"{type(exc).__name__}: request failed"
        print(f"AI COMMENT ERROR: {type(exc).__name__}")
        return None, "OPENAI_REQUEST_ERROR"
    except (ValueError, TypeError, KeyError) as exc:
        state["errors"] = state.get("errors", 0) + 1
        state["last_error"] = f"{type(exc).__name__}: response parsing failed"
        print(f"AI COMMENT ERROR: {type(exc).__name__}")
        return None, "OPENAI_RESPONSE_ERROR"


def append_ai_comment_log(post, band, comment, source, reason=""):
    fields = [
        "timestamp_utc", "post_id", "permalink", "review_band", "target_decision",
        "buyer_score", "follower_score", "source", "validation", "generated_comment",
    ]
    exists = os.path.exists(AI_COMMENT_LOG_FILE) and os.path.getsize(AI_COMMENT_LOG_FILE) > 0
    with open(AI_COMMENT_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "post_id": post.get("id", ""),
            "permalink": post.get("permalink", ""),
            "review_band": band,
            "target_decision": (
                "BUYER_AND_FOLLOWER" if buyer_link_qualified and follower_qualified
                else "BUYER" if buyer_link_qualified
                else "FOLLOWER" if follower_qualified
                else "NONE"
            ),
            "buyer_score": post.get("buyer_score", ""),
            "follower_score": post.get("follower_score", ""),
            "source": source,
            "validation": reason,
            "generated_comment": comment or "",
        })


def run_comment_review_dataset(posts):
    """Build review comments using OpenAI for a small qualified subset, with local fallback."""
    rows = []
    generated_comments = []
    persistent_comments = load_comment_history_memory()
    comparison_memory = persistent_comments + HISTORICAL_COMMENT_MEMORY
    ai_state = load_ai_action_state()
    ai_candidates = 0
    ai_successes = 0
    ai_fallbacks = 0
    ai_skipped = 0

    # AI is a COMMENT WRITER, not the audience decision-maker.
    # Do not require target_decision or the old comment_quality_pass here:
    # those fields can remain NONE until the final action engine runs.
    #
    # A post becomes an AI candidate when:
    #   - safety has passed
    #   - the account is eligible for engagement
    #   - Buyer OR Follower opportunity reaches the serious-review floor
    #   - the post has enough substantive text for the model to react to
    #
    # This deliberately keeps OpenAI out of the bulk discovery set while
    # allowing it to evaluate genuine Medium/High/Very-High opportunities.
    candidates = []
    for post in posts:
        if not post.get("safety_pass"):
            continue

        if post.get("account_type") not in {"PERSONAL", "CREATOR", "INFLUENCER"}:
            continue

        buyer_score = int(post.get("buyer_score", 0) or 0)
        follower_score = int(post.get("follower_score", 0) or 0)
        best_score = max(buyer_score, follower_score)

        if best_score < COMMENT_QUEUE_MIN_SCORE:
            continue

        band = comment_review_band(post)
        if band not in AI_MIN_REVIEW_BAND:
            continue

        caption = normalize_text(post.get("caption", ""))
        if len(caption.split()) < 12:
            continue

        # Business/competitor posts should never reach the AI writer even if
        # an upstream field was incomplete.
        if post.get("account_type") in {
            "BUSINESS", "BRAND", "RETAILER", "MANUFACTURER", "SERVICE_PROVIDER"
        }:
            continue

        candidates.append((post, band))

    # Highest-quality candidates first; never send the whole discovery set to AI.
    order = {"VERY_HIGH": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    candidates.sort(
        key=lambda item: (
            order.get(item[1], 0),
            max(item[0].get("buyer_score", 0), item[0].get("follower_score", 0)),
        ),
        reverse=True,
    )

    for post, band in candidates:
        if ai_candidates >= MAX_AI_COMMENT_CALLS_PER_RUN:
            ai_skipped += 1
            break

        ai_candidates += 1
        comment, source_reason = openai_generate_comment(
            post,
            ai_state,
            generated_comments + comparison_memory,
        )

        if comment:
            ai_successes += 1
            generated_comments.append(comment)
            post["suggested_comment"] = comment
            source = "OPENAI"
        else:
            ai_fallbacks += 1
            fallback = generate_unique_review_comment(
                post,
                generated_comments + comparison_memory,
            )
            if fallback:
                fallback_valid, fallback_reason = validate_ai_comment(
                    fallback,
                    post,
                    generated_comments + comparison_memory,
                )
                if not fallback_valid:
                    fallback = None
                    source_reason = f"LOCAL_FALLBACK_{fallback_reason}"
            comment = fallback
            post["suggested_comment"] = comment
            source = "LOCAL_FALLBACK" if fallback else "NO_COMMENT"

        if comment:
            generated_comments.append(comment) if comment not in generated_comments else None
            rows.append({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "generator_version": COMMENT_GENERATOR_VERSION,
                "post_id": post.get("id", ""),
                "permalink": post.get("permalink", ""),
                "buyer_score": post.get("buyer_score", ""),
                "buyer_band": post.get("buyer_intent", ""),
                "follower_score": post.get("follower_score", ""),
                "follower_band": post.get("follower_opportunity", ""),
                "review_band": band,
                "target_decision": (
                "BUYER_AND_FOLLOWER" if buyer_link_qualified and follower_qualified
                else "BUYER" if buyer_link_qualified
                else "FOLLOWER" if follower_qualified
                else "NONE"
            ),
                "account_type": post.get("account_type", ""),
                "categories": ",".join(post.get("categories", [])),
                "generated_comment": comment,
                "caption": post.get("caption", ""),
                "writer_source": source,
            })
            append_ai_comment_log(post, band, comment, source, source_reason)

    order = {"VERY_HIGH": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    rows.sort(key=lambda r: order.get(r["review_band"], 0), reverse=True)
    rows = rows[:COMMENT_REVIEW_LIMIT]

    fields = [
        "timestamp_utc", "generator_version", "post_id", "permalink",
        "buyer_score", "buyer_band", "follower_score", "follower_band",
        "review_band", "target_decision", "account_type", "categories",
        "generated_comment", "caption", "writer_source",
    ]

    with open(COMMENT_REVIEW_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    history_exists = os.path.exists(COMMENT_HISTORY_FILE) and os.path.getsize(COMMENT_HISTORY_FILE) > 0
    with open(COMMENT_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not history_exists:
            writer.writeheader()
        writer.writerows(rows)

    save_ai_action_state(ai_state)

    counts = Counter(row["review_band"] for row in rows)
    print()
    print("=" * 60)
    print("HYBRID AI COMMENT REVIEW DATASET V4.5")
    print("=" * 60)
    print("No comments were posted.")
    print(f"Qualified AI candidates: {len(candidates)}")
    print(f"OpenAI calls this run: {ai_candidates}")
    print(f"OpenAI successful comments: {ai_successes}")
    print(f"Local fallback comments: {ai_fallbacks}")
    print(f"AI candidates skipped by run cap: {ai_skipped}")
    print(f"OpenAI calls today: {ai_state.get('calls', 0)}/{MAX_AI_COMMENT_CALLS_PER_DAY}")
    print(f"  VERY HIGH: {counts.get('VERY_HIGH', 0)}")
    print(f"  HIGH:      {counts.get('HIGH', 0)}")
    print(f"  MEDIUM:    {counts.get('MEDIUM', 0)}")
    print(f"  LOW:       {counts.get('LOW', 0)}")
    print(f"Saved current run to: {COMMENT_REVIEW_FILE}")
    print(f"Appended to persistent history: {COMMENT_HISTORY_FILE}")

    for index, row in enumerate(rows[:20], start=1):
        print(
            f"[{index}] {row['review_band']} | {row['writer_source']} | "
            f"buyer={row['buyer_score']} | follower={row['follower_score']}"
        )
        print(f"  {row['generated_comment']}")
        print(f"  {row['permalink']}")

    return rows


def comment_quality_gate(post):
    """Decide whether a qualified buyer/follower is also worth commenting on.

    This is intentionally NOT a third audience score. Buyer and Follower are
    the only audience scores. Commenting is an action-quality gate applied
    after at least one of those two audiences qualifies.
    """
    if post.get("account_type") not in {"PERSONAL", "CREATOR", "INFLUENCER"}:
        return False, "INELIGIBLE_ACCOUNT", []

    text = normalize_text(post.get("caption", ""))
    categories = set(post.get("categories", []))
    product_matches = post.get("product_matches", [])
    reasons = []

    # V4.3.27: score-aware comment gate. High/Very-High opportunities
    # deserve an AI attempt even when the caption does not contain an
    # explicit question. Medium opportunities keep the stricter hook gate.
    conversation_hooks = [
        "what do you think", "tell me", "let me know", "who else",
        "anyone else", "can you relate", "which one", "what do you use",
        "what are you using", "comment", "would you", "have you", "do you",
        "what is your", "what's your", "drop yours", "tell me yours",
        "share yours", "your take", "thoughts",
    ]
    hook_hits = detect_phrases(text, conversation_hooks)

    buyer_score = int(post.get("buyer_score", 0) or 0)
    follower_score = int(post.get("follower_score", 0) or 0)
    best_score = max(buyer_score, follower_score)

    if hook_hits:
        reasons.append("conversational-hook")
    elif best_score >= 70:
        # High / Very-High: let the AI determine whether the post supports a
        # natural, specific comment. Do not require an explicit question.
        reasons.append("high-score-comment-opportunity")
    else:
        # Medium and below retain the stricter conversation requirement.
        return False, "NO_CONVERSATIONAL_HOOK", reasons

    specific_subject = bool(product_matches) or any(
        c in categories for c in ["hair", "beauty", "fashion", "motherhood", "humor", "womanhood"]
    )
    if specific_subject:
        reasons.append("specific-subject")
    else:
        return False, "NO_SPECIFIC_SUBJECT", reasons

    if len(text) >= 80:
        reasons.append("substantive-caption")
    else:
        return False, "CAPTION_TOO_THIN", reasons

    promotional = detect_phrases(
        text,
        ["shop now", "buy now", "discount", "promo code", "link in bio",
         "click the link", "book now", "book me"],
    )
    if promotional:
        reasons.append("promotional-context")
        return False, "PROMOTIONAL_CONTEXT", reasons

    # The AI writer is invoked later, after the audience decision is complete.
    # This gate only decides whether the post is a suitable comment candidate.
    post["suggested_comment"] = None
    reasons.append("ready-for-comment-writer")
    return True, "PASS", reasons


def final_decision(post):
    safe, safety_reason = safety_gate(post)
    post["safety_pass"] = safe
    post["safety_reason"] = safety_reason

    if not safe:
        post["buyer_decision"] = "BLOCKED"
        post["follower_decision"] = "BLOCKED"
        post["target_decision"] = "BLOCKED"
        post["action_decision"] = "NO_ACTION"
        post["comment_quality_pass"] = False
        post["comment_quality_reason"] = "SAFETY_BLOCK"
        return post

    post["buyer_decision"] = buyer_action_decision(post["buyer_score"])
    post["follower_decision"] = follower_action_decision(post)

    buyer_qualified = post["buyer_decision"] == "LIKE_CANDIDATE"
    follower_qualified = post["follower_decision"] == "LIKE_CANDIDATE"

    if buyer_qualified and follower_qualified:
        post["target_decision"] = "BUYER_AND_FOLLOWER"
    elif buyer_qualified:
        post["target_decision"] = "BUYER"
    elif follower_qualified:
        post["target_decision"] = "FOLLOWER"
    else:
        post["target_decision"] = "NONE"

    if not (buyer_qualified or follower_qualified):
        post["action_decision"] = "NO_ACTION"
        post["comment_quality_pass"] = False
        post["comment_quality_reason"] = "NO_TARGET_SCORE"
        post["suggested_comment"] = None
        return post

    comment_pass, comment_reason, comment_reasons = comment_quality_gate(post)
    post["comment_quality_pass"] = comment_pass
    post["comment_quality_reason"] = comment_reason
    post["comment_quality_reasons"] = comment_reasons

    if comment_pass:
        post["action_decision"] = "LIKE_AND_COMMENT"
    else:
        post["action_decision"] = "LIKE"

    return post


# ============================================================
# HISTORY
# ============================================================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {
            "posts": {},
            "accounts": {},
        }

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        data.setdefault("posts", {})
        data.setdefault("accounts", {})
        return data
    except (OSError, ValueError):
        print("History file could not be read; starting with empty history.")
        return {"posts": {}, "accounts": {}}


def save_history(history):
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    os.replace(tmp, HISTORY_FILE)


def account_history(history, account_id=None, username=None):
    # Prefer the stable Instagram account ID. Fall back to username only
    # when Meta did not provide an owner ID. Never merge unknown accounts.
    account_id = str(account_id or "").strip()
    username = str(username or "").strip()

    if account_id:
        key = f"id:{account_id}"
    elif username:
        key = f"username:{normalize_text(username)}"
    else:
        return {
            "positive_actions": 0,
            "distinct_posts": 0,
            "posts": [],
        }

    return history["accounts"].get(
        key,
        {
            "positive_actions": 0,
            "distinct_posts": 0,
            "posts": [],
        },
    )


def post_was_actioned(history, post_id, action):
    record = history["posts"].get(str(post_id), {})
    return action in record.get("actions", [])


def record_planned_action(history, post, action, result="DRY_RUN_PLANNED"):
    post_id = str(post.get("id", ""))
    username = post.get("username") or ""
    account_id = str(post.get("account_id") or "").strip()
    if account_id:
        account_key = f"id:{account_id}"
    elif username:
        account_key = f"username:{normalize_text(username)}"
    else:
        account_key = ""

    history["posts"].setdefault(
        post_id,
        {
            "username": username,
            "account_id": account_id,
            "actions": [],
            "timestamps": [],
        },
    )

    # Enrich older post records when owner information becomes available.
    post_record = history["posts"][post_id]
    if username and not post_record.get("username"):
        post_record["username"] = username
    if account_id and not post_record.get("account_id"):
        post_record["account_id"] = account_id

    if action not in history["posts"][post_id]["actions"]:
        history["posts"][post_id]["actions"].append(action)

    history["posts"][post_id]["timestamps"].append(
        datetime.now(timezone.utc).isoformat()
    )

    if account_key:
        history["accounts"].setdefault(
            account_key,
            {
                "positive_actions": 0,
                "distinct_posts": 0,
                "posts": [],
            },
        )

        account = history["accounts"][account_key]

        if post_id not in account["posts"]:
            account["posts"].append(post_id)
            account["distinct_posts"] += 1

        if action in {"LIKE", "COMMENT"} and result in {
            "DRY_RUN_PLANNED",
            "SUCCESS",
        }:
            account["positive_actions"] += 1


# ============================================================
# DUPLICATE CAPTION DETECTION
# ============================================================

def normalized_caption_for_duplicate(caption):
    text = normalize_text(caption)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"#\w+", "", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def mark_duplicate_captions(posts):
    seen = {}
    duplicates = 0

    for post in posts:
        normalized = normalized_caption_for_duplicate(
            post.get("caption", "")
        )

        if not normalized:
            post["duplicate_caption"] = False
            continue

        if normalized in seen:
            post["duplicate_caption"] = True
            post["duplicate_of"] = seen[normalized]
            duplicates += 1
        else:
            post["duplicate_caption"] = False
            seen[normalized] = post.get("id")

    return duplicates


# ============================================================
# DISCOVERY
# ============================================================

def discover_posts():
    all_posts = {}
    successful = 0
    failed = 0

    for hashtag in CATEGORY_HASHTAGS:
        print()
        print(f"Searching #{hashtag}...")

        hashtag_id = search_hashtag(hashtag)

        if not hashtag_id:
            failed += 1
            error = getattr(graph_get, "last_error", None) or {}
            if error.get("code") == 4:
                print("Meta application request limit reached; stopping discovery early to conserve requests.")
                break
            continue

        print(f"#{hashtag} -> hashtag ID {hashtag_id}")

        posts = get_recent_hashtag_media(hashtag_id)

        if not posts:
            print(f"No posts retrieved for #{hashtag}.")
            failed += 1
            continue

        successful += 1
        print(f"Found {len(posts)} posts for #{hashtag}")

        for post in posts:
            post_id = post.get("id")
            if not post_id:
                continue

            if post_id not in all_posts:
                all_posts[post_id] = {
                    "id": post_id,
                    "caption": post.get("caption", ""),
                    "media_type": post.get("media_type", ""),
                    "permalink": post.get("permalink", ""),
                    "timestamp": post.get("timestamp", ""),
                    "hashtags": [],
                    "account_id": "",
                    "username": "",
                }

            existing = all_posts[post_id]
            if hashtag not in existing["hashtags"]:
                existing["hashtags"].append(hashtag)

    discovered_posts = list(all_posts.values())

    # Hashtag recent_media does not provide creator identity for arbitrary
    # public posts, and individual media lookups are not supported reliably
    # for those IDs. Do not issue hundreds of guaranteed-to-fail enrichment
    # requests. Account-level history is therefore only used when an account
    # identity is supplied by another supported workflow.
    print()
    print("Creator identity enrichment skipped for hashtag media (unsupported by this API flow).")

    return discovered_posts, successful, failed


# ============================================================
# ANALYSIS
# ============================================================

def analyze_posts(posts, history):
    duplicate_count = mark_duplicate_captions(posts)

    analyzed = []

    for post in posts:
        buyer = calculate_buyer_opportunity(
            post.get("caption", ""),
            post.get("hashtags", []),
        )
        post.update(buyer)

        # Prefer stable owner/account identity from Meta when available.
        # Username remains a fallback for compatibility with older records.
        account_type, hard_matches, business_matches = classify_account(
            post.get("caption", ""),
            post.get("username", ""),
            {},
        )

        post["account_type"] = account_type
        post["business_matches"] = business_matches
        post["hard_business_matches"] = hard_matches

        history_record = account_history(
            history,
            post.get("account_id", ""),
            post.get("username", ""),
        )

        follower_score, follower_label, follower_matches = (
            calculate_follower_opportunity(
                post.get("caption", ""),
                post["categories"],
                account_type,
                business_matches,
                history_record,
            )
        )

        post["follower_score"] = follower_score
        post["follower_opportunity"] = follower_label
        post["follower_matches"] = follower_matches

        # Comments are evaluated only as an action-quality gate after the two
        # audience scores are calculated. There is no third Comment Score.
        post["suggested_comment"] = None
        post["comment_quality_pass"] = False
        post["comment_quality_reason"] = "NOT_EVALUATED"
        post["comment_quality_reasons"] = []

        # Duplicate engagement is checked per POST + ACTION.
        post["duplicate_post"] = (
            post_was_actioned(history, post["id"], "LIKE")
            or post_was_actioned(history, post["id"], "COMMENT")
        )

        post = final_decision(post)
        analyzed.append(post)

    # Buyer score remains the primary display ordering.
    analyzed.sort(
        key=lambda p: (
            max(p["buyer_score"], p["follower_score"]),
            p["action_decision"] == "LIKE_AND_COMMENT",
        ),
        reverse=True,
    )

    return analyzed, duplicate_count


# ============================================================
# REPORTING
# ============================================================

def append_audit(row):
    fields = [
        "timestamp_utc",
        "post_id",
        "permalink",
        "username",
        "account_type",
        "buyer_score",
        "buyer_intent",
        "follower_score",
        "follower_opportunity",
        "target_decision",
        "action_decision",
        "comment_quality_pass",
        "comment_quality_reason",
        "safety_pass",
        "safety_reason",
        "buyer_decision",
        "follower_decision",
    ]

    exists = os.path.exists(AUDIT_FILE)

    with open(AUDIT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)

        if not exists:
            writer.writeheader()

        writer.writerow({field: row.get(field, "") for field in fields})


def print_post(post, number):
    print()
    print(f"[{number}]")
    print(f"Buyer Opportunity:   {post['buyer_score']} ({post['buyer_intent']})")
    print(
        f"Follower Opportunity:{post['follower_score']} "
        f"({post['follower_opportunity']})"
    )
    print(f"Target Decision:     {post['target_decision']}")
    print(f"Action Decision:     {post['action_decision']}")
    print(f"Comment Quality:     {post['comment_quality_reason']}")
    print(f"Account Type:        {post['account_type']}")
    print(
        "Categories:          "
        + (", ".join(post["categories"]) if post["categories"] else "none")
    )
    print(f"Safety:              {post['safety_reason']}")

    print(
        f"Decisions:           "
        f"BUYER={post['buyer_decision']} | "
        f"FOLLOWER={post['follower_decision']} | "
        f"TARGET={post['target_decision']} | "
        f"ACTION={post['action_decision']}"
    )

    if post["follower_matches"]:
        print(
            "Follower Reasons:    "
            + ", ".join(post["follower_matches"][:6])
        )

    if post.get("comment_quality_reasons"):
        print(
            "Comment Gate:        "
            + ", ".join(post["comment_quality_reasons"][:6])
        )

    if post["suggested_comment"]:
        print(f"Suggested Comment:   {post['suggested_comment']}")

    print(f"Post:                {post['permalink']}")

    caption = post.get("caption", "")
    if len(caption) > 350:
        caption = caption[:350] + "..."

    print(f"Caption:             {caption}")


def print_summary(posts, successful, failed, duplicate_count):
    buyer_counts = Counter(p["buyer_intent"] for p in posts)
    follower_counts = Counter(p["follower_opportunity"] for p in posts)
    safe_posts = [p for p in posts if p["safety_pass"]]

    buyer_like_candidates = sum(
        1 for p in safe_posts if p["buyer_decision"] == "LIKE_CANDIDATE"
    )

    follower_like_candidates = sum(
        1 for p in safe_posts if p["follower_decision"] == "LIKE_CANDIDATE"
    )

    like_comment_candidates = sum(
        1 for p in safe_posts if p["action_decision"] == "LIKE_AND_COMMENT"
    )

    blocked_sensitive = sum(
        1 for p in posts if p["safety_reason"] == "SENSITIVE_CONTENT"
    )

    blocked_business = sum(
        1 for p in posts if p["safety_reason"] == "BUSINESS_OR_COMPETITOR"
    )

    print()
    print("=" * 60)
    print("BEAUTYROCKET V4.5 SUMMARY")
    print("=" * 60)

    print(f"Unique posts discovered: {len(posts)}")
    print(f"Hashtags successful:     {successful}")
    print(f"Hashtags failed/skipped: {failed}")
    print(f"Duplicate captions:      {duplicate_count}")

    print()
    print("BUYER")
    print(f"  VERY HIGH: {buyer_counts.get('VERY_HIGH', 0)}")
    print(f"  HIGH:      {buyer_counts.get('HIGH', 0)}")
    print(f"  MEDIUM:    {buyer_counts.get('MEDIUM', 0)}")
    print(f"  LOW:       {buyer_counts.get('LOW', 0)}")
    print(f"  IGNORE:    {buyer_counts.get('IGNORE', 0)}")

    print()
    print("FOLLOWER")
    print(f"  VERY HIGH: {follower_counts.get('VERY_HIGH', 0)}")
    print(f"  HIGH:      {follower_counts.get('HIGH', 0)}")
    print(f"  MEDIUM:    {follower_counts.get('MEDIUM', 0)}")
    print(f"  LOW:       {follower_counts.get('LOW', 0)}")
    print(f"  IGNORE:    {follower_counts.get('IGNORE', 0)}")

    print()
    print("COMMENT ACTION GATE")
    print(f"  LIKE + COMMENT candidates: {like_comment_candidates}")

    print()
    print("ACTION PLAN")
    print(f"  Buyer-driven like candidates:    {buyer_like_candidates}")
    print(f"  Follower-driven like candidates: {follower_like_candidates}")
    print(f"  Like + Comment candidates:       {like_comment_candidates}")

    print()
    print("SAFETY")
    print(f"  Sensitive blocked:        {blocked_sensitive}")
    print(f"  Business/competitor:      {blocked_business}")

    print()
    print(f"DRY RUN: {DRY_RUN}")
    if DRY_RUN:
        print("NO LIKES OR COMMENTS WERE SENT.")
    print("=" * 60)



def utc_day_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_action_state():
    default = {
        "date": utc_day_key(),
        "likes": 0,
        "comments": 0,
        "actions": 0,
        "consecutive_errors": 0,
        "controlled_comment_attempts": 0,
        "controlled_comment_test_id": CONTROLLED_COMMENT_TEST_ID,
        "last_action_at": None,
    }

    if not os.path.exists(STATE_FILE):
        return default

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") != utc_day_key():
            return default

        state = {**default, **state}

        # Each controlled comment validation is isolated by version. This
        # prevents a failed/expired-token attempt from V4.3.28 or V4.3.29
        # from blocking the first legitimate V4.5 test. Preserve the
        # existing like/comment/action counters so unrelated live safeguards
        # remain intact.
        if state.get("controlled_comment_test_id") != CONTROLLED_COMMENT_TEST_ID:
            state["controlled_comment_attempts"] = 0
            state["controlled_comment_test_id"] = CONTROLLED_COMMENT_TEST_ID
            state["consecutive_errors"] = 0

        return state
    except (OSError, ValueError):
        return default


def save_action_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def live_like_allowed(state):
    if state["likes"] >= MAX_LIKES_PER_DAY:
        return False
    if state["actions"] >= MAX_ACTIONS_PER_RUN:
        return False
    if state["consecutive_errors"] >= MAX_CONSECUTIVE_API_ERRORS:
        return False
    if CONTROLLED_LIVE_TEST and state["likes"] >= CONTROLLED_LIVE_MAX_LIKES:
        return False
    return True


def live_comment_allowed(state):
    if state["comments"] >= MAX_COMMENTS_PER_DAY:
        return False
    if state["actions"] >= MAX_ACTIONS_PER_RUN:
        return False
    if state["consecutive_errors"] >= MAX_CONSECUTIVE_API_ERRORS:
        return False
    if CONTROLLED_LIVE_TEST and state.get("controlled_comment_attempts", 0) >= CONTROLLED_LIVE_MAX_COMMENTS:
        return False
    return True


def enforce_action_delay(state):
    """Wait between live actions; never delays dry-run execution."""
    if state.get("last_action_at") is None:
        return
    try:
        last = datetime.fromisoformat(state["last_action_at"])
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    except (TypeError, ValueError):
        return
    remaining = MIN_SECONDS_BETWEEN_ACTIONS - elapsed
    if remaining > 0:
        print(f"LIVE ACTION DELAY: waiting {remaining:.1f}s before next action.")
        time.sleep(remaining)


def execute_live_comment(post, state, message):
    """V4.5 safety guard: automatic third-party comment posting is disabled."""
    print("LIVE COMMENT BLOCKED: V4.5 uses human manual posting from the review webpage.")
    return "DISABLED_V4_5"


def execute_live_like(post, state):
    if not LIVE_ENGAGEMENT or DRY_RUN:
        return "DRY_RUN_PLANNED"

    if not PAGE_ACCESS_TOKEN:
        print("LIVE ACTION BLOCKED: FACEBOOK_PAGE_ACCESS_TOKEN is not configured.")
        return "CONFIG_ERROR"

    if not live_like_allowed(state):
        return "LIMIT_BLOCKED"

    enforce_action_delay(state)
    result = like_media(post["id"])
    state["last_action_at"] = datetime.now(timezone.utc).isoformat()
    state["actions"] += 1

    if result and result.get("success") is True:
        state["likes"] += 1
        state["consecutive_errors"] = 0
        # Persist cumulative production output for the local dashboard.
        production_stats = load_production_stats()
        production_stats["total_likes"] = int(production_stats.get("total_likes", 0) or 0) + 1
        production_stats["last_run_likes"] = int(production_stats.get("last_run_likes", 0) or 0) + 1
        save_production_stats(production_stats)
        return "SUCCESS"

    state["consecutive_errors"] += 1
    return "FAILED"



# ============================================================
# PRODUCTION DASHBOARD STATS
# ============================================================

def load_production_stats():
    default = {
        "total_likes": 0,
        "total_comment_suggestions": 0,
        "total_runs": 0,
        "last_run_likes": 0,
        "last_run_comment_suggestions": 0,
        "last_run_at_utc": None,
        "last_run_new_queue_items": 0,
    }
    if not os.path.exists(PRODUCTION_STATS_FILE):
        return default
    try:
        with open(PRODUCTION_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**default, **data}
    except (OSError, ValueError, TypeError):
        return default


def save_production_stats(stats):
    tmp = PRODUCTION_STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, indent=2, fp=f)
    os.replace(tmp, PRODUCTION_STATS_FILE)


def sync_to_cloud(queue_items, production_stats):
    """
    Send newly discovered comment opportunities and cumulative production
    statistics to the authenticated Render dashboard.

    This is deliberately one-way. A cloud-sync failure never stops the local
    Instagram discovery/like workflow; local JSON files remain the fallback.
    """
    if not BOT_API_KEY:
        print("CLOUD SYNC: disabled (BEAUTYROCKET_BOT_API_KEY not configured).")
        return False

    payload = {
        "queue_items": queue_items if isinstance(queue_items, list) else [],
        "stats": production_stats if isinstance(production_stats, dict) else {},
    }

    try:
        response = requests.post(
            f"{CLOUD_DASHBOARD_URL}/api/ingest",
            json=payload,
            headers={"X-Beautyrocket-Bot-Key": BOT_API_KEY},
            timeout=CLOUD_SYNC_TIMEOUT,
        )

        if response.ok:
            try:
                result = response.json()
            except ValueError:
                result = {}
            print(
                "CLOUD SYNC: SUCCESS | "
                f"queue_items={result.get('queue_items_accepted', 0)} | "
                f"stats_saved={result.get('stats_saved', False)}"
            )
            return True

        print(f"CLOUD SYNC: FAILED | HTTP {response.status_code}")
        return False

    except requests.RequestException as exc:
        print(f"CLOUD SYNC: NETWORK FAILURE | {type(exc).__name__}")
        return False


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 64)
    print("BEAUTYROCKET DISCOVERY ENGINE V4.6 â€” HUMAN ENGAGEMENT OPPORTUNITIES")
    print("DISCOVER â†’ SCORE â†’ AI COMMENT â†’ HUMAN DECIDES")
    print("NO AUTOMATIC LIKES OR COMMENTS")
    print("=" * 64)
    print(f"DISCOVERY/REVIEW MODE: ACTIVE")
    print(f"AUTOMATIC INSTAGRAM ENGAGEMENT: DISABLED")

    if not ACCESS_TOKEN:
        print("ERROR: INSTAGRAM_ACCESS_TOKEN is not configured.")
        return

    if not INSTAGRAM_ACCOUNT_ID:
        print("ERROR: INSTAGRAM_ACCOUNT_ID is not configured.")
        return

    if not OPENAI_API_KEY:
        print("WARNING: OPENAI_API_KEY is not configured; AI comments will use the local fallback.")

    history = load_history()
    production_stats = load_production_stats()
    production_stats["total_runs"] = int(production_stats.get("total_runs", 0) or 0) + 1
    production_stats["last_run_likes"] = 0
    production_stats["last_run_comment_suggestions"] = 0
    production_stats["last_run_new_queue_items"] = 0
    production_stats["last_run_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_production_stats(production_stats)

    posts, successful, failed = discover_posts()

    print()
    print("=" * 64)
    print(f"UNIQUE POSTS DISCOVERED: {len(posts)}")
    print(f"HASHTAGS SUCCESSFUL:     {successful}")
    print(f"HASHTAGS FAILED/SKIPPED: {failed}")
    print("=" * 64)

    if not posts:
        print("No posts available for analysis.")
        return

    analyzed, duplicate_count = analyze_posts(posts, history)

    print()
    print("=" * 64)
    print("TOP V4.6 OPPORTUNITIES")
    print("=" * 64)

    for index, post in enumerate(analyzed[:20], start=1):
        print_post(post, index)

    # Generate AI suggestions for the strongest human opportunities.
    # The AI is a writer only; it never performs an Instagram action.
    run_comment_review_dataset(analyzed)

    print()
    print("=" * 64)
    print("V4.6 HUMAN OPPORTUNITY QUEUE")
    print("=" * 64)

    # An opportunity is created when the post is high enough for a human
    # engagement decision. Existing scoring remains the decision engine:
    #   - BUYER score >= 75 and/or FOLLOWER score >= 75 => LIKE opportunity
    #   - an available AI comment => LIKE + COMMENT
    #   - no AI comment => LIKE only
    #   - the human may use, edit, or ignore the AI suggestion.
    queue_candidates = []
    for post in analyzed:
        if not post.get("safety_pass"):
            continue

        buyer_score = int(post.get("buyer_score", 0) or 0)
        follower_score = int(post.get("follower_score", 0) or 0)

        comment_text = (
            post.get("suggested_comment")
            or post.get("ai_comment")
            or post.get("review_comment")
            or ""
        ).strip()

        buyer_link_qualified = (
            buyer_score >= COMMENT_QUEUE_MIN_SCORE
            and comment_has_verified_product_link(comment_text)
        )
        follower_qualified = follower_score >= COMMENT_QUEUE_MIN_SCORE
        like_opportunity = buyer_link_qualified or follower_qualified
        # Queue only opportunities that qualify as Potential Buyer or Engagement.
        # Comment quality alone must never admit a nonqualifying item.
        if not like_opportunity:
            continue

        # V4.6 engagement labels have exactly two outcomes:
        #   - AI comment exists -> LIKE + COMMENT
        #   - no AI comment -> LIKE
        # The dashboard must never show LIKE + COMMENT unless there is an
        # actual AI-generated/local-fallback comment available to copy.
        if comment_text:
            opportunity_type = "LIKE + COMMENT"
        elif like_opportunity:
            opportunity_type = "LIKE"
        else:
            # A comment-quality candidate without an AI comment is not a
            # usable dashboard opportunity because there is no suggested
            # comment to support the COMMENT action.
            continue

        best_score = max(buyer_score, follower_score)
        queue_candidates.append({
            "queue_id": hashlib.sha256(
                f"{post.get('id','')}|V4.6_OPPORTUNITY".encode("utf-8")
            ).hexdigest()[:16],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "PENDING",
            "post_id": str(post.get("id", "")),
            "permalink": post.get("permalink", ""),
            "buyer_score": buyer_score,
            "buyer_band": post.get("buyer_intent", ""),
            "follower_score": follower_score,
            "follower_band": post.get("follower_opportunity", ""),
            "best_score": best_score,
            "target_decision": (
                "BUYER_AND_FOLLOWER" if buyer_link_qualified and follower_qualified
                else "BUYER" if buyer_link_qualified
                else "FOLLOWER" if follower_qualified
                else "NONE"
            ),
            "target_action": opportunity_type,
            "action_decision": post.get("action_decision", ""),
            "account_type": post.get("account_type", ""),
            "categories": post.get("categories", []),
            "safety_pass": bool(post.get("safety_pass")),
            "suggested_comment": comment_text,
            "caption": post.get("caption", ""),
            "writer_source": post.get("writer_source", "AI"),
            "opportunity_type": opportunity_type,
            "human_action": "PENDING",
            "ai_comment_available": bool(comment_text),
        })

    queue_candidates.sort(
        key=lambda item: (
            item["best_score"],
            item["buyer_score"],
            item["follower_score"],
            item["opportunity_type"],
        ),
        reverse=True,
    )

    # Permanent post-level dedup. One Instagram post = one opportunity,
    # regardless of hashtag duplication or a later AI comment change.
    existing_queue = []
    if os.path.exists(COMMENT_QUEUE_FILE):
        try:
            with open(COMMENT_QUEUE_FILE, "r", encoding="utf-8") as f:
                existing_queue = json.load(f)
            if not isinstance(existing_queue, list):
                existing_queue = []
        except (OSError, ValueError, TypeError):
            existing_queue = []

    new_queue_items = 0
    blocked_post_ids = {
        str(item.get("post_id", "")).strip()
        for item in existing_queue
        if str(item.get("post_id", "")).strip()
        and item.get("status") in {"PENDING", "POSTED", "SKIPPED", "DONE"}
    }

    merged_queue = list(existing_queue)
    merged_post_ids = set(blocked_post_ids)
    for item in queue_candidates:
        post_id = str(item.get("post_id", "")).strip()
        if not post_id or post_id in merged_post_ids:
            continue
        merged_queue.append(item)
        merged_post_ids.add(post_id)
        new_queue_items += 1

    # Collapse historical duplicates while preserving the strongest status.
    deduped_queue = []
    seen_post_ids = {}
    priority = {"DONE": 4, "SKIPPED": 4, "POSTED": 3, "PENDING": 1}
    for item in merged_queue:
        post_id = str(item.get("post_id", "")).strip()
        if not post_id:
            deduped_queue.append(item)
            continue
        existing_index = seen_post_ids.get(post_id)
        if existing_index is None:
            seen_post_ids[post_id] = len(deduped_queue)
            deduped_queue.append(item)
            continue
        existing_item = deduped_queue[existing_index]
        if priority.get(item.get("status"), 0) > priority.get(existing_item.get("status"), 0):
            deduped_queue[existing_index] = item

    merged_queue = deduped_queue

    # Normalize historical/local queue records to the same two dashboard
    # outcomes. This also repairs older PENDING rows created before V4.6's
    # AI-comment availability rule was enforced.
    for item in merged_queue:
        comment_text = str(item.get("suggested_comment") or "").strip()
        item["ai_comment_available"] = bool(comment_text)
        item["opportunity_type"] = "LIKE + COMMENT" if comment_text else "LIKE"
        item["target_action"] = item["opportunity_type"]

    pending = [item for item in merged_queue if item.get("status") == "PENDING"]
    completed = [item for item in merged_queue if item.get("status") != "PENDING"]
    pending.sort(
        key=lambda item: (
            int(item.get("best_score", 0) or 0),
            int(item.get("buyer_score", 0) or 0),
            int(item.get("follower_score", 0) or 0),
        ),
        reverse=True,
    )
    merged_queue = pending[:100] + completed[-500:]

    tmp_queue = COMMENT_QUEUE_FILE + ".tmp"
    with open(tmp_queue, "w", encoding="utf-8") as f:
        json.dump(merged_queue, f, indent=2, ensure_ascii=False)
    os.replace(tmp_queue, COMMENT_QUEUE_FILE)

    # Dashboard statistics now measure opportunities, not bot-performed Likes.
    production_stats["last_run_likes"] = 0
    production_stats["last_run_new_queue_items"] = new_queue_items
    production_stats["last_run_comment_suggestions"] = new_queue_items
    production_stats["total_comment_suggestions"] = int(production_stats.get("total_comment_suggestions", 0) or 0) + new_queue_items
    save_production_stats(production_stats)

    print(f"NEW HUMAN OPPORTUNITIES ADDED: {new_queue_items}")
    print(f"TOTAL POTENTIAL OPPORTUNITIES: {production_stats['total_comment_suggestions']}")
    print(f"PENDING OPPORTUNITIES: {sum(1 for x in merged_queue if x.get('status') == 'PENDING')}")
    print("AUTOMATIC LIKES: DISABLED")
    print("AUTOMATIC COMMENTS: DISABLED")
    print("USER DECISION: LIKE, COMMENT, BOTH, OR NEITHER")

    if queue_candidates:
        for index, item in enumerate(queue_candidates[:25], start=1):
            print(
                f"[{index}] {item['opportunity_type']} | "
                f"buyer={item['buyer_score']} | follower={item['follower_score']} | "
                f"AI_COMMENT={'YES' if item['ai_comment_available'] else 'NO'} | "
                f"{item['permalink']}"
            )
    else:
        print("No high-value human opportunities currently qualify.")

    # The cloud dashboard is the human review surface. It receives the
    # opportunities only; it never performs Instagram actions.
    production_stats = load_production_stats()
    cloud_sync_ok = sync_to_cloud(queue_candidates[:100], production_stats)
    print(f"CLOUD SYNC STATUS: {'SUCCESS' if cloud_sync_ok else 'FAILED/NOT CONFIGURED'}")

    print_summary(
        analyzed,
        [],
        [],
        duplicate_count,
    )

    print()
    print("=" * 64)
    print("V4.6 COMPLETE")
    print("MODE: HUMAN-IN-THE-LOOP ENGAGEMENT")
    print("AUTOMATIC LIKES: DISABLED")
    print("AUTOMATIC COMMENTS: DISABLED")
    print("AI COMMENTS: SUGGESTIONS ONLY")
    print("DASHBOARD: POTENTIAL OPPORTUNITIES")
    latest_stats = load_production_stats()
    print("TOTAL POTENTIAL OPPORTUNITIES: ", latest_stats.get("total_comment_suggestions", 0))
    print("=" * 64)


if __name__ == "__main__":
    main()

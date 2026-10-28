"""Conservative Buyer qualification based on a verified catalog URL in the comment."""
import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parents[2]
MATCHER = ROOT / "ProductCatalog" / "product_matcher_v1_1.py"
URL_RE = re.compile(r"https?://[^\s<>\]\[()\"']+", re.I)
TOKENS = re.compile(r"[a-z0-9]+")

@lru_cache(maxsize=1)
def _verified_products():
    if not MATCHER.exists():
        return []
    spec = importlib.util.spec_from_file_location("beautyrocket_product_matcher", MATCHER)
    if not spec or not spec.loader:
        return []
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    loaded = mod.load_latest_catalog()
    if not isinstance(loaded, tuple) or len(loaded) != 2:
        return []
    _, products = loaded
    return [
        p for p in (products or [])
        if isinstance(p, dict) and p.get("catalog_verified_url") is True
    ]

def _url(value):
    try:
        p = urlparse(str(value).rstrip(".,!?;:"))
        if p.scheme.lower() != "https" or (p.hostname or "").lower() != "shop.saloninteractive.com":
            return None
        path = re.sub(r"/+", "/", unquote(p.path)).rstrip("/")
        return "https://shop.saloninteractive.com" + path
    except Exception:
        return None

def _tokens(value):
    return {x for x in TOKENS.findall(unquote(str(value or "")).lower()) if len(x) > 2}

def comment_has_verified_product_link(comment, products=None):
    """Only URLs in the actual comment qualify; missing catalog data fails closed."""
    urls = [x for x in (_url(u) for u in URL_RE.findall(str(comment or ""))) if x]
    if not urls:
        return False
    try:
        products = _verified_products() if products is None else products
    except Exception:
        return False
    products = [p for p in (products or [])
                if isinstance(p, dict) and p.get("catalog_verified_url") is True]
    if not products:
        return False
    for url in urls:
        for p in products:
            if _url(p.get("product_url", "")) == url:
                return True
    return False

def instagram_card_label(item, products=None):
    try:
        buyer = int(item.get("buyer_score", 0) or 0)
    except (TypeError, ValueError):
        buyer = 0
    try:
        follower = int(item.get("follower_score", 0) or 0)
    except (TypeError, ValueError):
        follower = 0
    if buyer >= 60 and comment_has_verified_product_link(item.get("suggested_comment", ""), products):
        return "Potential Buyer"
    if follower >= 75:
        return "Engagement"
    return None

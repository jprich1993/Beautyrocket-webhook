import json
import re
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "Data"
REPORTS = ROOT / "Reports"
VERSION = "1.1.0"

STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have",
    "has", "are", "was", "were", "you", "your", "its", "into",
    "product", "hair", "need", "looking", "want", "help", "best",
    "what", "which", "how", "can", "does", "about", "after",
}

TOPIC_TERMS = {
    "curl": {"curl", "curly", "curls", "coil", "coily", "definition"},
    "moisture": {"dry", "dryness", "moisture", "hydration", "thirsty"},
    "frizz": {"frizz", "frizzy", "smooth", "smoothing", "flyaways"},
    "repair": {"damage", "damaged", "repair", "breakage", "brittle", "strength"},
    "scalp": {"scalp", "itchy", "itching", "flakes", "flaking", "dandruff"},
    "cleansing": {"shampoo", "cleanse", "cleansing", "buildup", "build-up"},
    "conditioning": {"conditioner", "conditioning", "detangle", "detangling"},
    "oil": {"oil", "oils", "serum", "shine", "ends"},
    "heat": {"heat", "thermal", "blowout", "straightener", "heat-protection"},
    "color": {"color", "colour", "blonde", "brass", "toning", "colored"},
    "styling": {"style", "styling", "hold", "pomade", "gel", "wax", "volume"},
    "beard": {"beard", "shaving", "shave", "grooming"},
    "hair_loss": {"thinning", "shedding", "hairloss", "hair-loss"},
}

def tokenize(value):
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return {w for w in words if len(w) > 2 and w not in STOP_WORDS}

def load_latest_catalog():
    files = sorted(
        DATA.glob("product_catalog_v1_1_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(
            f"No product_catalog_v1_1_*.json found in {DATA}"
        )
    path = files[0]
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    records = obj.get("products", [])
    products = []
    seen_urls = set()

    for row in records:
        if not isinstance(row, dict):
            continue
        name = str(row.get("product_name") or "").strip()
        url = str(row.get("product_url") or "").strip()
        if not name or not url.startswith(
            "https://shop.saloninteractive.com/store/myhairhobby/product/"
        ):
            continue
        # Exclude products explicitly labeled unavailable in the catalog name.
        if re.search(r"\bout of stock\b|\bdiscontinued\b", name, re.I):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        prices = []
        for value in row.get("prices_seen") or []:
            try:
                number = float(value)
                if number > 0:
                    prices.append(number)
            except (TypeError, ValueError):
                pass

        products.append({
            "product_name": name,
            "product_url": url,
            "catalog_verified_url": row.get("verified_product_url") is True,
            "prices_seen": sorted(set(prices)),
            "description": str(row.get("description") or "").strip(),
            "brand": str(row.get("brand") or "").strip(),
            "availability": row.get("availability", "unknown"),
            "_tokens": tokenize(name),
        })

    return path, products

def shortlist_products(lead_text, products, limit=10):
    """Return name-matched catalog candidates; never invent product claims."""
    if limit < 1:
        return []

    lead_tokens = tokenize(lead_text)
    if not lead_tokens:
        return []

    lead_lower = str(lead_text or "").lower()
    matched_topics = {
        topic for topic, words in TOPIC_TERMS.items()
        if words.intersection(lead_tokens)
    }

    ranked = []
    for product in products:
        name = product["product_name"]
        name_lower = name.lower()
        product_tokens = product.get("_tokens") or tokenize(name)
        overlap = lead_tokens.intersection(product_tokens)

        topic_hits = 0
        for topic in matched_topics:
            terms = TOPIC_TERMS[topic]
            if terms.intersection(product_tokens):
                topic_hits += 1

        score = (len(overlap) * 2) + (topic_hits * 3)

        # Conservative topical matches based on explicit product names.
        if "curl" in matched_topics and any(
            term in name_lower for term in ("curl", "curly", "defining cream")
        ):
            score += 3
        if "scalp" in matched_topics and any(
            term in name_lower for term in ("scalp", "anti-flake", "dandruff")
        ):
            score += 3
        if "moisture" in matched_topics and any(
            term in name_lower for term in ("moisture", "hydration", "hydrating")
        ):
            score += 2
        if "repair" in matched_topics and any(
            term in name_lower for term in ("repair", "strengthening", "bond")
        ):
            score += 2
        if "heat" in matched_topics and any(
            term in name_lower for term in ("heat protection", "thermal")
        ):
            score += 3

        if score <= 0:
            continue

        ranked.append((score, product))

    ranked.sort(key=lambda pair: (
        pair[0],
        len(lead_tokens.intersection(pair[1].get("_tokens") or set())),
        pair[1]["product_name"].lower(),
    ), reverse=True)

    output = []
    for score, product in ranked[:limit]:
        output.append({
            "product_name": product["product_name"],
            "product_url": product["product_url"],
            "catalog_match_score": score,
            "match_basis": "product-name token/topic overlap; candidate only",
            "catalog_verified_url": product["catalog_verified_url"],
            "prices_seen": product["prices_seen"],
            "description": product["description"],
            "brand": product["brand"],
            "availability": product["availability"],
        })
    return output

def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    source, products = load_latest_catalog()

    examples = [
        ("curly hair needs curl definition", "curl"),
        ("dry hair needs moisture and hydration", "moisture"),
        ("itchy scalp and dandruff flakes", "scalp"),
        ("heat protection before blow drying", "heat"),
        ("beard grooming and shaving", "beard"),
    ]

    results = []
    for query, label in examples:
        matches = shortlist_products(query, products, limit=5)
        results.append({
            "test_case": label,
            "query": query,
            "matches": matches,
        })

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = REPORTS / f"product_matcher_v{VERSION}_{stamp}.json"
    output = {
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "catalog_file": str(source),
        "catalog_product_count": len(products),
        "matching_method": "conservative product-name token and topic overlap",
        "ratings_used": False,
        "availability_verified": False,
        "explicit_unavailable_name_filter": True,
        "test_examples": results,
        "limitations": [
            "Catalog crawl is incomplete.",
            "Descriptions and ratings are not currently populated.",
            "Name-based shortlist scores are not AI confidence or proof of product efficacy.",
            "Observed listing prices may represent different sizes or variants.",
            "AI must select only among supplied catalog candidates or return no match.",
        ],
    }
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Matcher version: {VERSION}")
    print(f"Catalog source: {source}")
    print(f"Unique usable products: {len(products)}")
    print(f"Report: {output_path}")

    for result in results:
        print(f"\n=== {result['test_case'].upper()} ===")
        if not result["matches"]:
            print("No candidate matched.")
        for match in result["matches"]:
            print(
                f"{match['catalog_match_score']:>3} | "
                f"{match['product_name']} | {match['product_url']}"
            )

if __name__ == "__main__":
    main()

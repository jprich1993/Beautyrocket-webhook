import sys
import json
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instagram_discovery as bot

PRODUCT_URL = (
    "https://shop.saloninteractive.com/store/myhairhobby/"
    "product/american-crew-conditioner"
)
PRODUCT = {
    "product_name": "American Crew Conditioner",
    "product_url": PRODUCT_URL,
    "catalog_verified_url": True,
}

def mock_response(comment):
    response = Mock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {
        "output_text": comment,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }
    return response

def run_case(buyer, follower, products, output):
    post = {
        "id": "offline-test",
        "caption": (
            "My hair has been feeling so dry lately and I am trying "
            "to figure out a better hair care routine for wash day."
        ),
        "categories": ["hair"],
        "product_matches": [],
        "buyer_score": buyer,
        "follower_score": follower,
    }
    state = {"calls": 0, "errors": 0}

    with patch.object(bot, "ai_comment_allowed", return_value=(True, "TEST")), \
         patch.object(bot, "_load_buyer_catalog_candidates", return_value=products), \
         patch.object(bot.requests, "post", return_value=mock_response(output)) as api:
        result, reason = bot.openai_generate_comment(post, state, [])

    return result, reason, api

# 1. Buyer with an exact verified catalog URL.
comment = f"This conditioner may be worth checking out for your routine: {PRODUCT_URL}"
result, reason, api = run_case(60, 20, [PRODUCT], comment)
assert result == comment, ("Verified buyer link should pass", reason)
assert api.call_count == 1
payload = api.call_args.kwargs["json"]
prompt = " ".join(
    item["content"] for item in payload["input"]
)
assert "BUYER COMMENT RULES" in prompt
assert "emotionally appropriate" in prompt.lower()
print("PASS: buyer with verified catalog link")

# 2. Buyer with a similar-looking but unverified URL.
bad_url = PRODUCT_URL + "-plus"
result, reason, api = run_case(
    60, 20, [PRODUCT],
    f"This could be useful for your routine: {bad_url}"
)
assert result is None, "Unverified buyer URL must be rejected"
assert "VERIFIED_LINK" in reason, reason
print("PASS: buyer with unverified URL rejected")

# 3. Buyer with no suitable catalog candidate must not call the AI.
result, reason, api = run_case(
    60, 20, [],
    "That wash day struggle is real, hope you find something that helps!"
)
assert result is None
assert reason == "BUYER_NO_VERIFIED_PRODUCT_MATCH", reason
assert api.call_count == 0
print("PASS: buyer without product match produces no comment")

# 4. Follower comments must not contain product links.
result, reason, api = run_case(
    20, 75, [],
    f"That wash day struggle is real, hope you find something that helps! {PRODUCT_URL}"
)
assert result is None, "Follower comment with link must be rejected"
assert reason == "FOLLOWER_COMMENT_MUST_NOT_INCLUDE_LINK", reason
print("PASS: follower comment with link rejected")

# 5. Follower without a link keeps the existing conversational style.
follower_comment = (
    "Wash day can turn into a whole project sometimes 😂 hope you find your routine!"
)
result, reason, api = run_case(20, 75, [], follower_comment)
assert result == follower_comment, ("Follower comment should pass", reason)
prompt = " ".join(
    item["content"] for item in api.call_args.kwargs["json"]["input"]
)
assert "FOLLOWER ENGAGEMENT RULES" in prompt
assert "warm, sincere, and human" in prompt
print("PASS: follower comment without link; emotional style retained")

print("\nPASS: all 5 mocked generator checks")
print("No real OpenAI requests or Instagram actions were made.")

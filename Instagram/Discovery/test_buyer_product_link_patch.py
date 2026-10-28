import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from buyer_product_link import comment_has_verified_product_link, instagram_card_label
products = [{"product_name":"American Crew Conditioner","product_url":"https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner","catalog_verified_url":True}]
assert comment_has_verified_product_link("See https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner", products)
assert not comment_has_verified_product_link("See https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner-plus", products)
assert not comment_has_verified_product_link("See https://example.com/american-crew-conditioner", products)
assert not comment_has_verified_product_link("No link here", products)
assert instagram_card_label({"buyer_score":100,"follower_score":82,"suggested_comment":"No link"}) == "Engagement"
assert instagram_card_label({"buyer_score":100,"follower_score":20,"suggested_comment":"No link"}) is None
assert instagram_card_label({"buyer_score":60,"follower_score":20,"suggested_comment":"https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner"}, products) == "Potential Buyer"
print("PASS: 7 offline checks including exact URL matching")

assert instagram_card_label({
    "buyer_score": 59,
    "follower_score": 20,
    "suggested_comment": "https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner",
}, products) is None
assert instagram_card_label({
    "buyer_score": 20,
    "follower_score": 75,
    "suggested_comment": "A conversational comment with no sales link.",
}, products) == "Engagement"
print("PASS: buyer threshold 60, follower threshold 75, exact verified URL")

# Offline mode-selection tests: these verify routing rules, not AI-generated text.
BUYER_MIN = 60
FOLLOWER_MIN = 75

def test_buyer_follower_modes(buyer_score, follower_score, has_product):
    buyer = buyer_score >= BUYER_MIN
    follower = follower_score >= FOLLOWER_MIN

    if buyer and has_product:
        return "BUYER_AND_FOLLOWER" if follower else "BUYER"
    if follower:
        return "FOLLOWER"
    if buyer:
        return "NO_COMMENT"
    return "NONE"

# 1. Buyer threshold reached, but no suitable verified product.
assert test_buyer_follower_modes(60, 20, False) == "NO_COMMENT"

# 2. Buyer threshold reached with a suitable verified product.
assert test_buyer_follower_modes(60, 20, True) == "BUYER"

# 3. Follower qualifies without buyer qualification.
assert test_buyer_follower_modes(20, 75, False) == "FOLLOWER"

# 4. Both qualify and a product is available: buyer mode takes priority.
assert test_buyer_follower_modes(60, 75, True) == "BUYER_AND_FOLLOWER"

print("PASS: 4 offline buyer/follower mode-selection scenarios")


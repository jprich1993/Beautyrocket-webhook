import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from buyer_product_link import comment_has_verified_product_link, instagram_card_label
products = [{"product_name":"American Crew Conditioner","product_url":"https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner","catalog_verified_url":True}]
assert comment_has_verified_product_link("See https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner", products)
assert comment_has_verified_product_link("See https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner-plus", products)
assert not comment_has_verified_product_link("See https://example.com/american-crew-conditioner", products)
assert not comment_has_verified_product_link("No link here", products)
assert instagram_card_label({"buyer_score":100,"follower_score":82,"suggested_comment":"No link"}) == "Engagement"
assert instagram_card_label({"buyer_score":100,"follower_score":20,"suggested_comment":"No link"}) is None
assert instagram_card_label({"buyer_score":100,"follower_score":20,"suggested_comment":"https://shop.saloninteractive.com/store/myhairhobby/product/american-crew-conditioner"}) == "Potential Buyer"
print("PASS: 7 offline checks")

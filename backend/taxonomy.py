"""
Fixed vocabularies the LLM must choose from. One place to change them.

CHANGE vs the design doc: your data contains complaint types the doc taxonomy has no home for
(payment failures, wrong checkout price / discount not applied, app crashes, return pickups, seller
not replying). Without new categories ~1 in 3 reviews would land in the "emerging" table.
Added: Returns, Platform. Added matching SEED_PROBLEMS. Delete them here if you disagree.
"""

ISSUE_TAXONOMY = {
    "Product": ["Size/Fit", "Quality", "Features", "Product Information"],
    "Delivery": ["Late Delivery", "Damaged Package", "Tracking"],
    "Refund": ["Refund Delay", "Incorrect Refund"],
    "Returns": ["Return Pickup"],
    "Service": ["Customer Service", "Communication"],
    "Platform": ["Payment", "Pricing", "App/Website"],
    "None": ["None"],
}

SENTIMENT = ["Positive", "Neutral", "Negative", "Mixed"]
SEVERITY = ["Low", "Medium", "High", "Critical"]

SEED_PROBLEMS = [
    "Incorrect Fit", "Delivery Delay", "Poor Product Quality", "Damaged Package", "Refund Delay",
    "Poor Customer Service", "No Specific Problem",
    # added for this dataset
    "Misleading Product Information", "Missing or Faulty Feature", "Tracking Issue",
    "Return Pickup Delay", "Seller Unresponsive", "Payment Failure", "Pricing Discrepancy", "App/Website Issue",
]

DECISIONS = ["INVESTIGATE", "MONITOR", "INSUFFICIENT_DATA"]
VERDICTS = ["SUPPORTED", "MIXED", "INCONCLUSIVE"]

# Dimensions we can compare complaint rates across (they exist on both reviews and orders).
# 'seller' is deliberately NOT here: the demo data has ~900 sellers with ~5 orders each, far below
# MIN_GROUP_ORDERS. Add "seller" back if your real catalogue has a handful of large sellers.
DIMENSIONS = ["category", "subcategory", "brand", "segment", "geography", "new_or_returning"]
BREAKDOWN_DIMS = ["category", "brand", "segment", "new_or_returning", "geography"]   # shown in the dropdown
CONCENTRATION_DIMS = ("category", "brand")
SHARED_FILTERS = set(DIMENSIONS)                       # exist on reviews AND orders
REVIEW_ONLY_FILTERS = {"source", "verified_purchase"}  # orders have no such columns
COMPARE_DIMENSIONS = BREAKDOWN_DIMS + ["source"]

# Shop wording -> taxonomy sub-category lives in insight.return_reason_map (database/schema.sql).

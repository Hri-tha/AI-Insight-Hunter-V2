"""Settings from .env. Same variable names as the old app, so the same Groq key works unchanged."""
import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# --- tunables (all the "starting guesses" from the design doc live here) ---
LAG_DAYS = 14                 # orders are counted from first review date minus this many days
MIN_GROUP_ORDERS = 30         # ignore groups with fewer orders than this (noise)
TAG_BATCH_SIZE = 10
DUPLICATE_DISTANCE = 0.08     # embedding distance below this = near-duplicate wording
PRODUCT_BURST_SIZE = 5        # reviews on one product in the same hour
REVIEWER_BURST_SIZE = 3       # reviews by one reviewer within 24 hours
DECISION_RATE_PCT = 5         # warning sign 1: problem is >= this % of reviews
DECISION_WOW_PCT = 20         # warning sign 2: week-over-week growth >= this %
MIN_REVIEWS_TO_JUDGE = 10


def require_settings():
    """Called by the app/scripts (not at import) so unit tests can import the backend without secrets."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing. Add it to your .env file (see .env.example).")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Add it to your .env file (see .env.example).")

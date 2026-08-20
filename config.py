# config.py — load everything from environment variables (never hardcode secrets)
import os

# ─── Telegram ────────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID     = os.environ.get("CHANNEL_ID", "")        # e.g. "@mychannel" or "-100123456"

# ─── AI Text (OpenRouter) ─────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL   = os.environ.get("OPENROUTER_MODEL", "openai/gpt-3.5-turbo")

# ─── Image background removal ─────────────────────────────────
REMOVEBG_API_KEY   = os.environ.get("REMOVEBG_API_KEY", "")

# ─── Image transformation (avoid copyright detection) ──────────
# Options: "auto", "oil_painting", "watercolor", "sketch", "vintage", "soft_glow", "none"
IMAGE_TRANSFORM_PRESET = os.environ.get("IMAGE_TRANSFORM_PRESET", "auto")
# Set to "0" to disable image transformation completely
IMAGE_TRANSFORM_ENABLED = os.environ.get("IMAGE_TRANSFORM_ENABLED", "1").strip() in ("1", "true", "yes")

# ─── Image generation fallback ────────────────────────────────
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")

# ─── Image hosting ────────────────────────────────────────────
IMGBB_API_KEY  = os.environ.get("IMGBB_API_KEY", "")

# ─── Google Sheets ────────────────────────────────────────────
GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "Amazon Bot")
JSON_FILE         = os.environ.get("JSON_FILE", "amazon-bot.json")   # path to service account JSON

# ─── Pinterest ────────────────────────────────────────────────
PINTEREST_BOARD         = os.environ.get("PINTEREST_BOARD", "Amazon Deals")
DEFAULT_PINTEREST_BOARD = PINTEREST_BOARD   # alias used by sheets_handler

# Optional: per-category board name overrides via environment variables
# Leave empty to use PINTEREST_BOARD as default for that category
PINTEREST_BOARD_ELECTRONICS = os.environ.get("PINTEREST_BOARD_ELECTRONICS", "")
PINTEREST_BOARD_HOME        = os.environ.get("PINTEREST_BOARD_HOME", "")
PINTEREST_BOARD_BEAUTY      = os.environ.get("PINTEREST_BOARD_BEAUTY", "")
PINTEREST_BOARD_FASHION     = os.environ.get("PINTEREST_BOARD_FASHION", "")
PINTEREST_BOARD_SPORTS      = os.environ.get("PINTEREST_BOARD_SPORTS", "")
PINTEREST_BOARD_KIDS        = os.environ.get("PINTEREST_BOARD_KIDS", "")
PINTEREST_BOARD_BOOKS       = os.environ.get("PINTEREST_BOARD_BOOKS", "")
PINTEREST_BOARD_PETS        = os.environ.get("PINTEREST_BOARD_PETS", "")

# ─── Amazon Affiliate ────────────────────────────────────────
AFFILIATE_TAG  = os.environ.get("AFFILIATE_TAG", "dazzledeals00-20")
MAX_RESULTS    = int(os.environ.get("MAX_RESULTS", "5"))

# ─── Scheduler defaults ──────────────────────────────────────
DEFAULT_INTERVAL_MINUTES = int(os.environ.get("DEFAULT_INTERVAL_MINUTES", "30"))

# ─── Anti-block delays (seconds) ─────────────────────────────
SCRAPE_DELAY_MIN = float(os.environ.get("SCRAPE_DELAY_MIN", "2.0"))
SCRAPE_DELAY_MAX = float(os.environ.get("SCRAPE_DELAY_MAX", "5.0"))

# ─── Pinterest API v5 (no Playwright, lightweight) ────────────────────────────
PINTEREST_ACCESS_TOKEN = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
PINTEREST_DAILY_CAP    = int(os.environ.get("PINTEREST_DAILY_CAP", "5"))
PINTEREST_HOURS_AHEAD  = float(os.environ.get("PINTEREST_HOURS_AHEAD", "4"))

# ─── Test mode ────────────────────────────────────────────────────────────────
# When TEST_MODE=1, pipeline runs without publishing to channel/WP/Blogger/Sheets/Pinterest
TEST_MODE = os.environ.get("TEST_MODE", "0").strip() in ("1", "true", "yes")

# ─── OpenRouter model (2026 best value) ───────────────────────────────────────
# Override with better model: e.g. "anthropic/claude-3-haiku", "mistralai/mistral-7b-instruct"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

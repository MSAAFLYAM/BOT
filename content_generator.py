"""
content_generator.py — Product description generator (module-level functions).
Fixes OpenRouter model: mistral-7b-instruct:free → gemma-3-27b-it:free
"""
from __future__ import annotations
import logging, os
import httpx

logger = logging.getLogger(__name__)

GROQ_KEY       = os.environ.get("GROQ_API_KEY","")
GROQ_KEY_2     = os.environ.get("GROQ_API_KEY_2","")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY","")

# Fixed models (mistral-7b-instruct:free is DECOMMISSIONED)
OPENROUTER_MODELS = [
    "google/gemma-3-27b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "qwen/qwen3-8b:free",
]
GROQ_MODEL = "llama-3.3-70b-versatile"

_key_idx = 0
def _groq_key():
    global _key_idx
    keys = [k for k in [GROQ_KEY, GROQ_KEY_2] if k]
    if not keys: return ""
    key = keys[_key_idx % len(keys)]
    _key_idx += 1
    return key


def _call_groq(prompt: str, max_tokens: int = 250) -> str:
    key = _groq_key()
    if not key: return ""
    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}","Content-Type":"application/json"},
            json={"model": GROQ_MODEL,
                  "messages":[{"role":"user","content":prompt}],
                  "max_tokens":max_tokens,"temperature":0.7},
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"[content] Groq: {e}")
    return ""


def _call_openrouter(prompt: str, max_tokens: int = 250) -> str:
    if not OPENROUTER_KEY: return ""
    for model in OPENROUTER_MODELS:
        try:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}",
                         "Content-Type":"application/json"},
                json={"model":model,
                      "messages":[{"role":"user","content":prompt}],
                      "max_tokens":max_tokens},
                timeout=25,
            )
            if r.status_code == 200:
                logger.info(f"[content] ✅ OpenRouter/{model}")
                return r.json()["choices"][0]["message"]["content"].strip()
            elif r.status_code == 404:
                continue
        except Exception as e:
            logger.warning(f"[content] OpenRouter/{model}: {e}")
    return ""


def _template_desc(title: str, price: str) -> str:
    p = f" priced at {price}" if price else ""
    return (
        f"Discover the {title}{p} — a top-rated Amazon product loved by thousands. "
        f"Designed for quality, reliability, and everyday use. "
        f"Whether you need it for personal use or as a gift, this product delivers outstanding value. "
        f"Order today and experience the difference!"
    )


# ── Module-level functions (backward compatible) ─────────────────────────────

def generate_description(title: str, price: str = "") -> str:
    """Generate product description. Always returns something."""
    prompt = (
        f"Write a 150-word Amazon affiliate product description in English.\n"
        f"Product: {title}\n"
        f"{'Price: ' + price if price else ''}\n"
        f"Be compelling, highlight benefits, add a call-to-action. No markdown."
    )
    result = _call_groq(prompt) or _call_openrouter(prompt)
    if result and len(result) > 40:
        logger.info(f"[content] ✅ Description: {len(result)} chars")
        return result
    return _template_desc(title, price)


def map_pinterest_board(title: str) -> str:
    """Map product to Pinterest board."""
    t = title.lower()
    if any(k in t for k in ["shoe","sneaker","boot","sandal","dress","shirt","jean"]):
        return "Fashion & Shoes"
    if any(k in t for k in ["kitchen","cook","pot","pan","blender","coffee","fryer"]):
        return "Kitchen & Home"
    if any(k in t for k in ["laptop","phone","tablet","cable","speaker","tech","gadget"]):
        return "Tech & Gadgets"
    if any(k in t for k in ["gym","yoga","fitness","sport","workout"]):
        return "Fitness & Health"
    if any(k in t for k in ["dog","cat","pet","puppy"]):
        return "Pet Products"
    if any(k in t for k in ["baby","kid","toy","child"]):
        return "Baby & Kids"
    if any(k in t for k in ["beauty","makeup","skincare","serum"]):
        return "Beauty & Skincare"
    return "Amazon Deals"


def extract_keywords(title: str) -> list[str]:
    """Extract keywords from title."""
    stops = {"for","and","the","with","in","of","to","a","an","by","best","new","top"}
    words = [w.lower() for w in title.split() if len(w) > 3]
    return [w for w in words if w not in stops][:5]

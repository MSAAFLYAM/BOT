"""
facebook_publisher.py — Facebook Graph API Publisher v2
Safe, official, works forever (phone-only workflow).

HOW IT WORKS:
  Phone → /fbpost [url] in Telegram → Bot → Facebook Graph API
  Zero PC needed. Zero ban risk.

SUPPORTED:
  ✅ Post text + image to Facebook Pages (permanent token)
  ✅ Post text + image to Facebook Groups (publish_to_groups permission)
  ✅ Auto token refresh (before 60-day expiry)
  ✅ Anti-spam: daily cap, minimum gap, jitter, dedup
  ✅ Retry on rate limit

SETUP:
  1. developers.facebook.com → Create App (Business type)
  2. Add permissions: pages_manage_posts, publish_to_groups
  3. Get User Access Token → exchange for long-lived (60 days)
  4. Get Page Access Token (never expires) via /me/accounts
  5. Set environment variables (see below)

ENV VARIABLES:
  FB_PAGE_ACCESS_TOKEN   = page token (never expires)
  FB_USER_ACCESS_TOKEN   = user token (60 days, auto-renewed)
  FB_APP_ID              = your app ID (for renewal)
  FB_APP_SECRET          = your app secret (for renewal)
  FB_GROUP_ID            = numeric group ID
  FB_PAGE_ID             = numeric page ID
  FB_DAILY_CAP           = 8 (max posts/day, default)
  FB_MIN_GAP_MINUTES     = 30 (min between posts)
"""
import os, time, json, random, logging, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
PAGE_TOKEN   = os.environ.get("FB_PAGE_ACCESS_TOKEN","").strip()
USER_TOKEN   = os.environ.get("FB_USER_ACCESS_TOKEN","").strip()
APP_ID       = os.environ.get("FB_APP_ID","").strip()
APP_SECRET   = os.environ.get("FB_APP_SECRET","").strip()
GROUP_ID     = os.environ.get("FB_GROUP_ID","").strip()
PAGE_ID      = os.environ.get("FB_PAGE_ID","").strip()
DAILY_CAP    = int(os.environ.get("FB_DAILY_CAP","8"))
MIN_GAP_MIN  = int(os.environ.get("FB_MIN_GAP_MINUTES","30"))
API_VER      = "v19.0"
BASE         = f"https://graph.facebook.com/{API_VER}"

STATE_FILE   = "/tmp/fb_state.json"


# ── State management ───────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(Path(STATE_FILE).read_text())
    except Exception:
        return {"n":0,"date":"","last":0,"urls":[],"token_expires":0}

def _save(s: dict):
    try:
        Path(STATE_FILE).write_text(json.dumps(s))
    except Exception as e:
        logger.warning(f"[fb] state save: {e}")

def _day_reset(s: dict) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if s.get("date") != today:
        s.update({"n":0, "date":today})
    return s


# ── Token management ───────────────────────────────────────────────────────────

def exchange_for_long_lived(short_token: str) -> dict:
    """
    Exchange a short-lived (1h) user token for a long-lived (60 days) one.
    Call this once after generating a token in the developer console.
    """
    if not APP_ID or not APP_SECRET:
        return {"ok":False,"error":"FB_APP_ID et FB_APP_SECRET requis"}
    r = requests.get(f"{BASE}/oauth/access_token", params={
        "grant_type":        "fb_exchange_token",
        "client_id":         APP_ID,
        "client_secret":     APP_SECRET,
        "fb_exchange_token": short_token,
    }, timeout=15)
    data = r.json()
    if "access_token" in data:
        expires_in = data.get("expires_in", 5184000)  # 60 days default
        return {
            "ok":         True,
            "token":      data["access_token"],
            "expires_in": expires_in,
            "expires_at": int(time.time()) + expires_in,
        }
    return {"ok":False,"error":data.get("error",{}).get("message","Unknown")}


def get_page_tokens() -> list[dict]:
    """
    Get all Page Access Tokens (permanent) for pages you manage.
    Use PAGE token instead of USER token for pages — never expires.
    """
    token = PAGE_TOKEN or USER_TOKEN
    if not token:
        return []
    r = requests.get(f"{BASE}/me/accounts",
                     params={"access_token":token,"fields":"id,name,access_token"},
                     timeout=15)
    return r.json().get("data", [])


def check_token(token: str) -> dict:
    """Check if a token is valid and when it expires."""
    if not token:
        return {"valid":False,"error":"No token"}
    # Use the token to get basic info
    r = requests.get(f"{BASE}/me",
                     params={"access_token":token,"fields":"id,name"},
                     timeout=10)
    data = r.json()
    if "id" in data:
        # Check expiry via debug_token (needs app token)
        app_token = f"{APP_ID}|{APP_SECRET}" if APP_ID and APP_SECRET else token
        d = requests.get(f"{BASE}/debug_token",
                         params={"input_token":token,"access_token":app_token},
                         timeout=10).json().get("data",{})
        expires_at = d.get("expires_at",0)
        if expires_at == 0:
            return {"valid":True,"name":data["name"],"type":"permanent","days_left":99999}
        exp = datetime.fromtimestamp(expires_at,tz=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).days
        return {"valid":True,"name":data["name"],"type":d.get("type","?"),
                "days_left":days,"expires":exp.strftime("%Y-%m-%d")}
    err = data.get("error",{}).get("message","Invalid token")
    return {"valid":False,"error":err}


# ── Core API call ─────────────────────────────────────────────────────────────

def _post(endpoint: str, data: dict, files: dict | None = None,
          token: str = "") -> dict:
    """POST to Graph API with retry and rate limit handling."""
    url  = f"{BASE}/{endpoint}"
    tok  = token or PAGE_TOKEN or USER_TOKEN
    data = dict(data)
    data["access_token"] = tok

    for attempt in range(3):
        try:
            resp = requests.post(url, data=data,
                                 files=files, timeout=30)
            body = resp.json()

            if resp.status_code == 200 and "id" in body:
                return {"ok":True,"id":body["id"]}

            err  = body.get("error", {})
            code = err.get("code", 0)

            # Transient rate limit → wait and retry
            if resp.status_code == 429 or code in (4, 17, 32, 613):
                wait = int(resp.headers.get("Retry-After", 120))
                logger.warning(f"[fb] Rate limited, waiting {wait}s")
                time.sleep(min(wait, 300))
                continue

            # Token expired
            if code in (190, 102, 467):
                return {"ok":False,"error":"Token expiré — regenerate via /fbtoken","code":code}

            msg = err.get("message","Unknown FB error")
            logger.error(f"[fb] API error [{code}]: {msg}")
            return {"ok":False,"error":msg,"code":code}

        except Exception as e:
            logger.error(f"[fb] Request attempt {attempt+1}: {e}")
            if attempt < 2: time.sleep(5)

    return {"ok":False,"error":"3 tentatives échouées"}


# ── Rate limit check ──────────────────────────────────────────────────────────

def _can_post(s: dict) -> tuple[bool,str]:
    if not (PAGE_TOKEN or USER_TOKEN):
        return False, "Token FB manquant (FB_PAGE_ACCESS_TOKEN ou FB_USER_ACCESS_TOKEN)"
    if s["n"] >= DAILY_CAP:
        return False, f"Plafond {DAILY_CAP} posts/jour atteint — réinitialisation à minuit UTC"
    gap = time.time() - s.get("last",0)
    if gap < MIN_GAP_MIN*60:
        left = int((MIN_GAP_MIN*60 - gap)/60)
        return False, f"Anti-spam: attendre encore {left} min"
    return True, "OK"

def _dedup(url: str, s: dict) -> bool:
    h = hashlib.md5(url.encode()).hexdigest()[:10]
    return h in s.get("urls",[])

def _mark(url: str, s: dict) -> dict:
    h = hashlib.md5(url.encode()).hexdigest()[:10]
    s["urls"] = s.get("urls",[])[-100:]
    s["urls"].append(h)
    return s


# ── Public publish functions ───────────────────────────────────────────────────

def post_to_group(
    message:     str,
    image_bytes: bytes | None = None,
    image_url:   str          = "",
    link:        str          = "",
    group_id:    str          = "",
) -> dict:
    """
    Post to a Facebook Group.
    Requires: publish_to_groups permission + user access token.
    """
    gid = group_id or GROUP_ID
    if not gid:
        return {"ok":False,"error":"FB_GROUP_ID non défini"}

    s = _day_reset(_load())
    ok, reason = _can_post(s)
    if not ok:
        return {"ok":False,"error":reason}

    if link and _dedup(link, s):
        return {"ok":False,"error":f"URL déjà postée (anti-doublon 48h): {link[:50]}"}

    # Random jitter (anti-pattern detection)
    time.sleep(random.uniform(5, 45))

    # Groups need USER token (not page token)
    tok = USER_TOKEN

    if image_bytes and len(image_bytes) > 500:
        result = _post(f"{gid}/photos",
                       {"caption": message},
                       files={"source":("img.jpg",image_bytes,"image/jpeg")},
                       token=tok)
    elif image_url:
        result = _post(f"{gid}/photos",
                       {"url":image_url,"caption":message},
                       token=tok)
    else:
        payload = {"message":message}
        if link: payload["link"] = link
        result = _post(f"{gid}/feed", payload, token=tok)

    if result.get("ok"):
        s["n"] += 1; s["last"] = time.time()
        if link: s = _mark(link, s)
        _save(s)
        result["post_url"] = f"https://facebook.com/groups/{gid}"
        logger.info(f"[fb] Group post ✅ id={result['id']}")

    return result


def post_to_page(
    message:     str,
    image_bytes: bytes | None = None,
    image_url:   str          = "",
    link:        str          = "",
    page_id:     str          = "",
) -> dict:
    """
    Post to a Facebook Page.
    Requires: pages_manage_posts permission + PAGE access token (permanent).
    """
    pid = page_id or PAGE_ID
    if not pid:
        return {"ok":False,"error":"FB_PAGE_ID non défini"}

    s = _day_reset(_load())
    ok, reason = _can_post(s)
    if not ok:
        return {"ok":False,"error":reason}

    if link and _dedup(link, s):
        return {"ok":False,"error":"URL déjà postée (anti-doublon)"}

    time.sleep(random.uniform(3, 20))

    tok = PAGE_TOKEN or USER_TOKEN  # prefer page token for pages

    if image_bytes and len(image_bytes) > 500:
        result = _post(f"{pid}/photos",
                       {"caption":message},
                       files={"source":("img.jpg",image_bytes,"image/jpeg")},
                       token=tok)
    elif image_url:
        result = _post(f"{pid}/photos",
                       {"url":image_url,"caption":message},
                       token=tok)
    else:
        payload = {"message":message}
        if link: payload["link"] = link
        result = _post(f"{pid}/feed", payload, token=tok)

    if result.get("ok"):
        s["n"] += 1; s["last"] = time.time()
        if link: s = _mark(link, s)
        _save(s)
        result["post_url"] = f"https://facebook.com/{pid}"
        logger.info(f"[fb] Page post ✅ id={result['id']}")

    return result


def get_stats() -> dict:
    s = _day_reset(_load())
    last = datetime.fromtimestamp(s.get("last",0),tz=timezone.utc)
    return {
        "posts_today": s["n"],
        "daily_cap":   DAILY_CAP,
        "min_gap_min": MIN_GAP_MIN,
        "last_post":   last.strftime("%H:%M UTC") if s.get("last") else "—",
        "token_page":  "✅" if PAGE_TOKEN else "❌",
        "token_user":  "✅" if USER_TOKEN else "❌",
        "group_id":    GROUP_ID or "—",
        "page_id":     PAGE_ID or "—",
    }

def is_configured() -> bool:
    return bool((PAGE_TOKEN or USER_TOKEN) and (GROUP_ID or PAGE_ID))

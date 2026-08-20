"""
pinterest_api.py — Pinterest API v5  (lightweight, no Playwright, no RAM overhead)
Pure HTTP via requests — runs in background automatically.

SETUP (one-time):
  1. https://developers.pinterest.com/apps/ → Create App
  2. Use OAuth Playground: https://developers.pinterest.com/tools/oauth-token-generator/
     Scopes needed: boards:read, pins:read, pins:write
  3. Set PINTEREST_ACCESS_TOKEN in environment variables
  4. Optional: PINTEREST_DAILY_CAP (default 5), PINTEREST_HOURS_AHEAD (default 4)
"""

import os, time, logging, requests
from datetime import datetime, timedelta, timezone
import config, sheets_handler

logger = logging.getLogger(__name__)

BASE          = "https://api.pinterest.com/v5"
DAILY_CAP     = int(os.environ.get("PINTEREST_DAILY_CAP", "5"))
HOURS_AHEAD   = float(os.environ.get("PINTEREST_HOURS_AHEAD", "4"))
_board_cache: dict[str, str] = {}
_board_ts: float = 0
CACHE_TTL = 3600


# ── Auth ──────────────────────────────────────────────────────────────────────

def _tok() -> str:
    t = os.environ.get("PINTEREST_ACCESS_TOKEN", "").strip()
    if not t:
        raise RuntimeError(
            "PINTEREST_ACCESS_TOKEN manquant.\n"
            "Génère un token sur https://developers.pinterest.com/tools/oauth-token-generator/\n"
            "puis ajoute-le dans les variables d'environnement."
        )
    return t

def _h() -> dict:
    return {"Authorization": f"Bearer {_tok()}", "Content-Type": "application/json"}

def is_configured() -> bool:
    return bool(os.environ.get("PINTEREST_ACCESS_TOKEN", "").strip())


# ── Connection test ───────────────────────────────────────────────────────────

def test_connection() -> tuple[bool, str]:
    if not is_configured():
        return False, "PINTEREST_ACCESS_TOKEN non défini"
    try:
        r = requests.get(f"{BASE}/user_account", headers=_h(), timeout=15)
        if r.status_code == 200:
            d = r.json()
            return True, f"@{d.get('username','?')} ({d.get('account_type','?')})"
        return False, f"HTTP {r.status_code}: {r.json().get('message','')}"
    except Exception as e:
        return False, str(e)


# ── Board management ──────────────────────────────────────────────────────────

def list_boards() -> list[dict]:
    try:
        out, params = [], {"page_size": 100}
        while True:
            r = requests.get(f"{BASE}/boards", headers=_h(), params=params, timeout=15)
            d = r.json()
            out.extend(d.get("items", []))
            bm = d.get("bookmark")
            if not bm:
                break
            params["bookmark"] = bm
        return out
    except Exception as e:
        logger.error(f"[pin_api] list_boards: {e}")
        return []

def get_board_id(name: str) -> str | None:
    global _board_cache, _board_ts
    if not _board_cache or time.time() - _board_ts > CACHE_TTL:
        _board_cache = {b["name"].lower().strip(): b["id"] for b in list_boards()}
        _board_ts = time.time()
        logger.info(f"[pin_api] Boards cached: {list(_board_cache.keys())}")
    return _board_cache.get(name.lower().strip())


# ── Pin creation ──────────────────────────────────────────────────────────────

def create_pin(
    board_name: str,
    title: str,
    description: str,
    image_url: str,
    link: str,
    keywords: str = "",
    publish_date: str = "",
) -> dict:
    """Returns {success, pin_id, url, error}"""
    bid = get_board_id(board_name)
    if not bid:
        # Auto-create board if it doesn't exist via API
        try:
            cr = requests.post(f"{BASE}/boards",
                               headers=_h(),
                               json={"name": board_name, "privacy": "PUBLIC"},
                               timeout=15)
            if cr.status_code in (200, 201):
                bid = cr.json().get("id", "")
                _board_cache[board_name.lower().strip()] = bid
                logger.info(f"[pin_api] Board créé: {board_name} → {bid}")
        except Exception:
            pass
    if not bid:
        return {"success": False, "pin_id": "", "url": "",
                "error": f"Board '{board_name}' introuvable et non créable"}

    payload: dict = {
        "board_id": bid,
        "title":    title[:100],
        "description": description[:500],
        "link":     link,
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    if keywords:
        payload["note"] = keywords[:500]
    if publish_date:
        payload["scheduled_publish_time"] = publish_date

    try:
        r = requests.post(f"{BASE}/pins", headers=_h(), json=payload, timeout=30)
        d = r.json()
        if r.status_code in (200, 201):
            pin_id = d.get("id", "")
            url    = f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else ""
            return {"success": True, "pin_id": pin_id, "url": url, "error": ""}
        return {"success": False, "pin_id": "", "url": "",
                "error": d.get("message", f"HTTP {r.status_code}")}
    except Exception as e:
        return {"success": False, "pin_id": "", "url": "", "error": str(e)}


# ── Scheduling helpers ────────────────────────────────────────────────────────

def calc_publish_date(slot: int = 0) -> str:
    """ISO 8601 UTC — now + HOURS_AHEAD + slot × (24h / DAILY_CAP)."""
    base    = datetime.now(timezone.utc) + timedelta(hours=HOURS_AHEAD)
    spacing = timedelta(hours=24 / max(1, DAILY_CAP))
    return (base + slot * spacing).strftime("%Y-%m-%dT%H:%M:%S")


# ── Bulk publish from Sheets ──────────────────────────────────────────────────

def publish_pending_api(max_pins: int | None = None, notify=None) -> dict:
    """
    Publish up to max_pins pending rows from Google Sheets via Pinterest API.
    notify(str) optional callback for real-time status messages.
    Returns {published, failed, skipped, errors}.
    """
    if not is_configured():
        return {"published": 0, "failed": 0, "skipped": 0,
                "errors": ["PINTEREST_ACCESS_TOKEN non configuré"]}

    limit = max_pins or DAILY_CAP
    res   = {"published": 0, "failed": 0, "skipped": 0, "errors": []}

    try:
        sheet   = sheets_handler.get_sheet()
        records = sheets_handler._safe_get_all_records(sheet)
    except Exception as e:
        res["errors"].append(f"Sheets: {e}")
        return res

    def _v(row, *keys):
        for k in keys:
            v = str(row.get(k) or "").strip()
            if v:
                return v
        return ""

    candidates = []
    for idx, row in enumerate(records):
        status    = str(row.get("pinterest_status", "") or "").lower().strip()
        image_url = _v(row, "Media URL", "Amazon Image")
        link      = _v(row, "Link", "WP Post URL")
        if status in ("", "pending") and image_url and link:
            candidates.append((idx + 2, row))
        else:
            res["skipped"] += 1
        if len(candidates) >= limit:
            break

    for slot, (sheet_row, row) in enumerate(candidates):
        title      = (_v(row, "Title") or "Amazon Deal")[:100]
        desc       = _v(row, "Description")[:500]
        image_url  = _v(row, "Media URL", "Amazon Image")
        link       = _v(row, "Link", "WP Post URL")
        board_name = (_v(row, "Pinterest Board")
                      or getattr(config, "PINTEREST_BOARD", "Amazon Deals"))
        keywords   = _v(row, "Keywords")
        pub_date   = calc_publish_date(slot)

        result = create_pin(board_name, title, desc, image_url, link, keywords, pub_date)

        if result["success"]:
            sheets_handler.mark_pinterest_published(
                str(row.get("ASIN", "") or ""), "published"
            )
            # Write publish date back to Publish Date column
            try:
                sh = sheets_handler.get_sheet()
                sh.update_cell(sheet_row, 7, pub_date[:10])  # G = Publish Date
            except Exception:
                pass
            res["published"] += 1
            msg = f"📌 ✅ L{sheet_row}: {title[:35]} → {pub_date[11:16]} UTC"
            logger.info(f"[pin_api] {msg}")
            if notify:
                notify(msg)
        else:
            sheets_handler.mark_pinterest_published(
                str(row.get("ASIN", "") or ""), "failed"
            )
            res["failed"] += 1
            err = f"L{sheet_row}: {result['error'][:60]}"
            res["errors"].append(err)
            logger.error(f"[pin_api] {err}")
            if notify:
                notify(f"📌 ❌ {err}")

    return res

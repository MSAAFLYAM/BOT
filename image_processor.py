# image_processor.py — Remove Amazon background, produce clean white-background JPEG
import requests
import base64
import io
import logging
from PIL import Image, ImageFilter, ImageDraw
import config

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.amazon.com/",
}

def download_image(url: str) -> bytes | None:
    if not url:
        return None
    try:
        res = requests.get(url, timeout=20, headers=_HEADERS)
        if res.status_code == 200 and res.content:
            logger.debug(f"[img] Downloaded {len(res.content)//1024} KB")
            return res.content
        logger.warning(f"[img] Download HTTP {res.status_code}")
    except Exception as e:
        logger.error(f"[img] Download error: {e}")
    return None

def _remove_bg_api(image_bytes: bytes) -> bytes | None:
    api_key = config.REMOVEBG_API_KEY
    if not api_key:
        return None
    try:
        resp = requests.post(
            "https://api.remove.bg/v1.0/removebg",
            files={"image_file": ("product.jpg", image_bytes, "image/jpeg")},
            data={"size": "auto"},
            headers={"X-Api-Key": api_key},
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info(f"[img] remove.bg OK, credits charged: {resp.headers.get('X-Credits-Charged','?')}")
            return resp.content
        logger.warning(f"[img] remove.bg HTTP {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        logger.error(f"[img] remove.bg error: {e}")
    return None

def _remove_bg_clipdrop(image_bytes: bytes) -> bytes | None:
    """
    Background removal via Clipdrop API (Stability AI).
    FREE: 500 removals/month — best free option available.

    Setup (one-time):
      1. Go to https://clipdrop.co/apis
      2. Sign up → API → Copy your key
      3. Add CLIPDROP_API_KEY to environment variables

    Quality: excellent (same engine as paid professional tools).
    """
    api_key = os.environ.get("CLIPDROP_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        resp = requests.post(
            "https://clipdrop-api.co/remove-background/v1",
            files={"image_file": ("image.jpg", image_bytes, "image/jpeg")},
            headers={"x-api-key": api_key},
            timeout=30,
        )
        if resp.status_code == 200 and resp.content:
            logger.info(f"[img] Clipdrop bg removed ✅ ({len(resp.content)//1024} KB)")
            return resp.content
        logger.warning(f"[img] Clipdrop HTTP {resp.status_code}: {resp.text[:80]}")
    except Exception as e:
        logger.error(f"[img] Clipdrop error: {e}")
    return None


def remove_background(image_bytes: bytes) -> bytes | None:
    """
    Remove background from an image.
    Priority chain:
      1. Clipdrop API (free 500/month, best quality) — set CLIPDROP_API_KEY
      2. remove.bg API (free 50/month)               — set REMOVEBG_API_KEY
      3. Local rembg (disabled by default, OOM risk)  — set ENABLE_REMBG=1
    Returns bytes with transparent background (PNG) or None if all fail.
    """
    result = _remove_bg_clipdrop(image_bytes)
    if result:
        return result
    result = _remove_bg_api(image_bytes)
    if result:
        return result
    return _remove_bg_local(image_bytes)
    # rembg loads a ~170MB ONNX model and is very memory-heavy.
    # On small hosts it OOM-kills the whole process mid-pipeline.
    # It is therefore DISABLED by default and only used if explicitly enabled.
    import os
    if os.environ.get("ENABLE_REMBG", "0").strip().lower() not in ("1", "true", "yes"):
        logger.debug("[img] local rembg disabled (set ENABLE_REMBG=1 to enable)")
        return None
    try:
        from rembg import remove as rembg_remove   # lazy import — only when enabled
        result = rembg_remove(image_bytes)
        logger.info("[img] rembg local OK")
        return result
    except ImportError:
        logger.warning("[img] rembg not installed — pip install rembg")
    except Exception as e:
        logger.error(f"[img] rembg error: {e}")
    return None

def _add_white_background(png_bytes: bytes,
                          output_size: tuple = (1200, 1200),
                          padding: int = 100,
                          shadow: bool = True) -> bytes:
    product = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = product.size
    ratio = min(
        (output_size[0] - 2 * padding) / w,
        (output_size[1] - 2 * padding) / h,
    )
    new_w, new_h = int(w * ratio), int(h * ratio)
    product_resized = product.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", output_size, (255, 255, 255, 255))
    x = (output_size[0] - new_w) // 2
    y = (output_size[1] - new_h) // 2

    if shadow:
        shadow_layer = Image.new("RGBA", output_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(shadow_layer)
        sx, sy = x + new_w * 0.1, y + new_h - 10
        ex, ey = x + new_w * 0.9, y + new_h + 20
        for i in range(12, 0, -1):
            alpha = int(18 * (i / 12))
            draw.ellipse([sx - i*2, sy - i, ex + i*2, ey + i], fill=(0, 0, 0, alpha))
        canvas = Image.alpha_composite(canvas, shadow_layer.filter(ImageFilter.GaussianBlur(6)))

    canvas.paste(product_resized, (x, y), product_resized)
    final = Image.new("RGB", output_size, (255, 255, 255))
    final.paste(canvas.convert("RGB"), (0, 0))
    out = io.BytesIO()
    # Save as PNG for high quality (no compression loss)
    final.save(out, format="PNG", optimize=False)
    return out.getvalue()

def process_product_image(amazon_img_url: str) -> bytes | None:
    original = download_image(amazon_img_url)
    if not original:
        logger.warning("[img] Could not download Amazon image")
        return None

    transparent = _remove_bg_api(original)
    if not transparent:
        logger.info("[img] Falling back to local rembg")
        transparent = _remove_bg_local(original)

    if not transparent:
        logger.warning("[img] Background removal failed — returning original")
        return original

    result = _add_white_background(transparent, output_size=(800, 800), padding=70, shadow=True)
    logger.info(f"[img] Final image: {len(result)//1024} KB")
    return result

def upload_to_imgbb(image_bytes: bytes, filename: str = "product.jpg") -> str:
    if not config.IMGBB_API_KEY or not image_bytes:
        return ""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": config.IMGBB_API_KEY, "image": b64, "name": filename},
            timeout=30,
        )
        if resp.status_code == 200:
            url = resp.json()["data"].get("url", "")
            logger.info(f"[img] ImgBB upload OK: {url}")
            return url
        logger.warning(f"[img] ImgBB HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"[img] ImgBB error: {e}")
    return ""


def upload_to_telegraph(image_bytes: bytes, filename: str = "card.jpg") -> str:
    """
    Upload image to Telegra.ph (Telegram's free image host).
    No API key required. Returns public URL or empty string on failure.
    Ideal fallback when ImgBB fails or key is missing.
    """
    if not image_bytes:
        return ""
    try:
        ext  = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
        resp = requests.post(
            "https://telegra.ph/upload",
            files={"file": (filename, image_bytes, mime)},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                url = f"https://telegra.ph{data[0]['src']}"
                logger.info(f"[img] Telegraph upload OK: {url}")
                return url
        logger.warning(f"[img] Telegraph HTTP {resp.status_code}: {resp.text[:80]}")
    except Exception as e:
        logger.error(f"[img] Telegraph error: {e}")
    return ""


def upload_to_catbox(image_bytes: bytes, filename: str = "card.png") -> str:
    """
    Upload to catbox.moe — free, no API key, very reliable.
    Anonymous uploads. Returns permanent public URL or "" on failure.
    """
    if not image_bytes:
        return ""
    try:
        ext  = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        mime = "image/jpeg" if ext in ("jpg","jpeg") else "image/png"
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (filename, image_bytes, mime)},
            timeout=30,
        )
        if resp.status_code == 200 and resp.text.startswith("https://"):
            url = resp.text.strip()
            logger.info(f"[img] Catbox upload OK: {url}")
            return url
        logger.warning(f"[img] Catbox HTTP {resp.status_code}: {resp.text[:60]}")
    except Exception as e:
        logger.error(f"[img] Catbox error: {e}")
    return ""


def upload_image(image_bytes: bytes, filename: str = "card.png") -> str:
    """
    Upload image with automatic fallback chain:
      1. ImgBB      (fastest, if IMGBB_API_KEY set — retry once on failure)
      2. Telegra.ph (free, no key)
      3. Catbox.moe (free, no key, very reliable)
    Returns the best available public URL, or "" if all fail.
    """
    import time

    if not image_bytes:
        return ""

    # 1. ImgBB (with 1 retry)
    if config.IMGBB_API_KEY:
        for attempt in range(2):
            url = upload_to_imgbb(image_bytes, filename)
            if url:
                return url
            if attempt == 0:
                logger.warning("[img] ImgBB attempt 1 failed — retrying in 2s")
                time.sleep(2)
        logger.warning("[img] ImgBB both attempts failed → trying fallbacks")

    # 2. Telegra.ph
    url = upload_to_telegraph(image_bytes, filename)
    if url:
        return url
    logger.warning("[img] Telegraph failed → trying Catbox")

    # 3. Catbox.moe (most reliable, no API key needed)
    url = upload_to_catbox(image_bytes, filename)
    if url:
        return url

    logger.error("[img] ❌ All upload methods failed (ImgBB/Telegraph/Catbox)")
    return ""

# image_transformer.py — Transform Amazon product images to avoid copyright detection
# Uses free AI tools + local PIL effects to make images unique while keeping products recognizable
import io
import logging
import random
import requests
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw
import numpy as np

logger = logging.getLogger(__name__)

# Free AI image transformation APIs (no API key required)
FREE_APIS = {
    "pollinations": "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&seed={seed}&nologo=true",
}


def _download_image(url: str) -> bytes | None:
    """Download image from URL."""
    if not url:
        return None
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.amazon.com/"
        }
        resp = requests.get(url, timeout=20, headers=headers)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.error(f"[transform] Download error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════
# LOCAL PIL TRANSFORMATIONS (no API needed, instant)
# ═══════════════════════════════════════════════════════════════

def _oil_painting_effect(img: Image.Image, brush_size: int = 4) -> Image.Image:
    """Apply oil painting effect using median filter + color quantization."""
    # Convert to RGB if needed
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # Apply median filter for brush stroke effect
    for _ in range(2):
        img = img.filter(ImageFilter.MedianFilter(size=brush_size))
    
    # Enhance saturation slightly
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.3)
    
    # Slight contrast boost
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.1)
    
    return img


def _watercolor_effect(img: Image.Image) -> Image.Image:
    """Apply watercolor-like effect using edge preservation + blur."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # Edge-preserving filter
    img = img.filter(ImageFilter.EDGE_ENHANCE_MORE)
    
    # Gentle blur
    img = img.filter(ImageFilter.GaussianBlur(radius=1.5))
    
    # Reduce color depth slightly
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.4)
    
    # Soft glow effect
    glow = img.filter(ImageFilter.GaussianBlur(radius=3))
    img = Image.blend(img, glow, alpha=0.2)
    
    return img


def _sketch_effect(img: Image.Image) -> Image.Image:
    """Apply pencil sketch effect."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # Convert to grayscale
    gray = img.convert("L")
    
    # Invert
    inverted = Image.eval(gray, lambda x: 255 - x)
    
    # Gaussian blur
    blurred = inverted.filter(ImageFilter.GaussianBlur(radius=21))
    
    # Dodge blend
    sketch = Image.new("L", img.size)
    pixels_sketch = sketch.load()
    pixels_gray = gray.load()
    pixels_blur = blurred.load()
    
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            g = pixels_gray[x, y]
            b = pixels_blur[x, y]
            if b == 255:
                pixels_sketch[x, y] = 255
            else:
                pixels_sketch[x, y] = min(255, (g * 256) // (256 - b))
    
    # Convert back to RGB and apply slight sepia
    result = Image.merge("RGB", [sketch, sketch, sketch])
    enhancer = ImageEnhance.Contrast(result)
    result = enhancer.enhance(1.5)
    
    return result


def _vintage_effect(img: Image.Image) -> Image.Image:
    """Apply vintage/retro filter."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # Reduce saturation
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(0.7)
    
    # Add warm tint
    r, g, b = img.split()
    r = r.point(lambda x: min(255, int(x * 1.1)))
    b = b.point(lambda x: int(x * 0.9))
    img = Image.merge("RGB", (r, g, b))
    
    # Slight vignette
    w, h = img.size
    vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(vignette)
    
    for i in range(min(w, h) // 3):
        alpha = int(80 * (i / (min(w, h) // 3)))
        draw.rectangle(
            [i, i, w - i, h - i],
            outline=(0, 0, 0, alpha)
        )
    
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, vignette)
    
    return img_rgba.convert("RGB")


def _soft_glow_effect(img: Image.Image) -> Image.Image:
    """Apply soft glow/bloom effect."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # Create glow layer
    glow = img.filter(ImageFilter.GaussianBlur(radius=8))
    
    # Brighten glow
    enhancer = ImageEnhance.Brightness(glow)
    glow = enhancer.enhance(1.3)
    
    # Blend
    result = Image.blend(img, glow, alpha=0.3)
    
    # Boost colors
    enhancer = ImageEnhance.Color(result)
    result = enhancer.enhance(1.2)
    
    return result


def _drop_shadow(img: Image.Image) -> Image.Image:
    """Add professional drop shadow around product."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    
    w, h = img.size
    shadow_offset = 8
    
    # Create shadow
    shadow = Image.new("RGBA", (w + shadow_offset * 2, h + shadow_offset * 2), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    
    # Draw shadow (offset)
    shadow_draw.rectangle(
        [shadow_offset, shadow_offset, w + shadow_offset, h + shadow_offset],
        fill=(0, 0, 0, 60)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=10))
    
    # Paste product on top
    shadow.paste(img, (0, 0), img)
    
    return shadow


# ═══════════════════════════════════════════════════════════════
# FREE AI API TRANSFORMATIONS (Pollinations.ai - no key needed)
# ═══════════════════════════════════════════════════════════════

def _pollinations_transform(image_bytes: bytes, style: str = "professional product photo") -> bytes | None:
    """
    Use Pollinations.ai to transform image with AI.
    Completely free, no API key needed.
    """
    try:
        # Pollinations doesn't support image-to-image directly,
        # but we can use their text-to-image with a product description
        # For now, we'll skip this and use local effects
        logger.debug("[transform] Pollinations AI not available for img2img, using local effects")
        return None
    except Exception as e:
        logger.error(f"[transform] Pollinations error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# MAIN TRANSFORM FUNCTION
# ═══════════════════════════════════════════════════════════════

# Available transformation presets
TRANSFORM_PRESETS = {
    "oil_painting": {
        "name": "Oil Painting",
        "description": "Artistic oil painting effect with brush strokes",
        "function": _oil_painting_effect,
    },
    "watercolor": {
        "name": "Watercolor",
        "description": "Soft watercolor painting effect",
        "function": _watercolor_effect,
    },
    "sketch": {
        "name": "Pencil Sketch",
        "description": "Hand-drawn pencil sketch effect",
        "function": _sketch_effect,
    },
    "vintage": {
        "name": "Vintage",
        "description": "Retro vintage filter with warm tones",
        "function": _vintage_effect,
    },
    "soft_glow": {
        "name": "Soft Glow",
        "description": "Professional soft glow/bloom effect",
        "function": _soft_glow_effect,
    },
}


def transform_image(
    image_bytes: bytes,
    preset: str = "auto",
    add_shadow: bool = True,
    output_format: str = "PNG",
    output_quality: int = 98,
) -> bytes | None:
    """
    Transform product image to avoid copyright detection.
    
    Presets:
        - "auto": Random selection from best presets
        - "oil_painting": Artistic brush stroke effect
        - "watercolor": Soft watercolor painting
        - "sketch": Pencil sketch effect
        - "vintage": Retro vintage filter
        - "soft_glow": Professional soft glow
        - "none": No transformation, just add shadow
    
    Returns: Transformed image bytes or None on failure
    """
    if not image_bytes:
        return None
    
    try:
        img = Image.open(io.BytesIO(image_bytes))
        original_size = img.size
        logger.info(f"[transform] Original image size: {original_size}")
        
        # Auto-select random preset
        if preset == "auto":
            preset = random.choice(list(TRANSFORM_PRESETS.keys()))
        
        # Apply transformation
        if preset != "none" and preset in TRANSFORM_PRESETS:
            transform_fn = TRANSFORM_PRESETS[preset]["function"]
            logger.info(f"[transform] Applying {TRANSFORM_PRESETS[preset]['name']} effect")
            img = transform_fn(img)
        
        # Add drop shadow
        if add_shadow and preset != "sketch":
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img = _drop_shadow(img)
        
        # Convert to output format
        if output_format == "JPEG":
            if img.mode == "RGBA":
                # Create white background for JPEG
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
        
        # Save with high quality
        out = io.BytesIO()
        if output_format == "PNG":
            # PNG is lossless - best quality
            img.save(out, format="PNG", optimize=False)
        else:
            # JPEG with high quality
            img.save(out, format="JPEG", quality=output_quality, optimize=False)
        
        result = out.getvalue()
        final_size = Image.open(io.BytesIO(result)).size
        logger.info(f"[transform] OK: {len(result)//1024} KB ({preset}) - Size: {final_size}")
        return result
        
    except Exception as e:
        logger.error(f"[transform] Error: {e}")
        return None


def transform_amazon_image(amazon_img_url: str, preset: str = "auto") -> bytes | None:
    """
    Download Amazon image and transform it.
    
    Returns: Transformed image bytes or None on failure
    """
    # Download original
    image_bytes = _download_image(amazon_img_url)
    if not image_bytes:
        logger.warning("[transform] Could not download Amazon image")
        return None
    
    # Transform
    return transform_image(image_bytes, preset=preset)


def get_available_presets() -> dict:
    """Return list of available transformation presets."""
    return {
        key: {
            "name": val["name"],
            "description": val["description"],
        }
        for key, val in TRANSFORM_PRESETS.items()
    }

# blogger_publisher.py â€” Article HTML builder shared by email publisher
# Used by: blogger_email_publisher.py â†’ _build_article()

import re
from datetime import datetime


def _stars(rating: float) -> str:
    full  = int(rating)
    half  = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "â˜…" * full + ("Â½" if half else "") + "â˜†" * empty


def _build_article(product: dict, description: str) -> tuple:
    """
    Build (title, html) for Blogger post.
    Called by blogger_email_publisher.publish_post().
    Returns inline-style HTML safe for Blogger.
    """
    title        = product.get("title", "Product") or "Product"
    price        = product.get("price", "N/A") or "N/A"
    orig_price   = product.get("original_price", "") or ""
    rating       = float(product.get("rating", 0) or 0)
    review_count = int(product.get("review_count", 0) or 0)
    aff_link     = product.get("aff_link", "#") or "#"
    img_url      = product.get("img_url", "") or ""
    year         = datetime.now().year

    seo_title = f"{title[:65]} â€” Best Price & Review ({year})"
    stars_str = _stars(rating)
    reviews_str = f"{review_count:,}" if review_count else "â€”"

    # Price display with discount
    price_block = f"<strong style='color:#c0392b;font-size:26px;'>{price}</strong>"
    if orig_price:
        price_block += f" &nbsp;<s style='color:#999;'>{orig_price}</s>"
        try:
            cp = float(re.sub(r"[^\d.]", "", price))
            op = float(re.sub(r"[^\d.]", "", orig_price))
            if op > cp > 0:
                pct = int(round((op - cp) / op * 100))
                price_block += (
                    f" &nbsp;<span style='background:#e74c3c;color:#fff;"
                    f"padding:2px 8px;border-radius:4px;font-size:13px;"
                    f"font-weight:bold;'>-{pct}% OFF</span>"
                )
        except Exception:
            pass

    img_block = ""
    if img_url:
        img_block = (
            f"<div style='text-align:center;margin:24px 0;'>"
            f"<img src='{img_url}' alt='{title[:60]}' "
            f"style='max-width:460px;width:100%;border-radius:12px;"
            f"box-shadow:0 4px 16px rgba(0,0,0,0.12);'/>"
            f"</div>"
        )

    html = f"""<div style="font-family:Arial,sans-serif;max-width:720px;margin:0 auto;line-height:1.7;color:#222;">

{img_block}

<p style="margin:0 0 16px;">{description}</p>

<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:12px;padding:24px;margin:24px 0;text-align:center;">
  <div style="margin-bottom:8px;">{price_block}</div>
  <div style="color:#f39c12;font-size:20px;letter-spacing:2px;margin-bottom:4px;">{stars_str}</div>
  <p style="color:#555;margin:0 0 16px;font-size:14px;">
    <strong>{rating}/5</strong> &nbsp;â€¢&nbsp; {reviews_str} verified reviews
  </p>
  <a href="{aff_link}"
     style="display:inline-block;background:#ff9900;color:#fff;
            text-decoration:none;padding:14px 32px;border-radius:50px;
            font-size:16px;font-weight:bold;">
    ðŸ›’ Check Price on Amazon â†’
  </a>
</div>

<h2 style="font-size:20px;color:#2c3e50;border-left:4px solid #ff9900;padding-left:12px;">
  Why We Recommend It
</h2>
<p style="margin:0 0 16px;">
  The <strong>{title[:60]}</strong> has earned strong reviews from {reviews_str} buyers.
  At {price}, it offers excellent value for the quality delivered.
</p>

<div style="text-align:center;margin:24px 0;">
  <a href="{aff_link}"
     style="display:inline-block;background:#ff9900;color:#fff;
            text-decoration:none;padding:14px 32px;border-radius:50px;
            font-size:16px;font-weight:bold;">
    ðŸ›’ Buy Now â€” {price} on Amazon
  </a>
</div>

<p style="font-size:11px;color:#aaa;text-align:center;margin-top:32px;
          border-top:1px solid #eee;padding-top:12px;">
  * As an Amazon Associate I earn from qualifying purchases.
  Prices accurate at publishing time and subject to change.
</p>

</div>"""

    return seo_title, html

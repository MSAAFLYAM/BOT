"""
pinterest/pins.py — Pinterest pin creation pipeline.

2 pin types:
  - ProductPin   : price, brand, affiliate link, rating
  - ArticlePin   : excerpt, read time, category

Architecture decisions:
  - Each pin type has its own description builder (optimized for engagement).
  - Image compressed via TinyPNG before being sent to Pinterest.
    Pinterest fetches the image URL — compressed = faster load.
  - Board selected automatically by content type (BoardManager).
  - Daily cap checked before each pin attempt (DailyScheduler).
  - DB status updated after pin creation (pinterest_status field).
  - Hashtags added to description (Pinterest SEO).

Pinterest pin best practices applied:
  - Description: 150-300 chars optimal (longer descriptions perform better)
  - Include 2-5 relevant hashtags
  - Include call-to-action ("Découvrez", "Cliquez", "Voir le produit")
  - Include keyword in first 50 chars
  - Link goes to affiliate URL or article URL

Results:
  - PinResult dataclass with pin_id, pin_url, board_id
  - pin_url format: https://www.pinterest.com/pin/{pin_id}/
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scraping.amazon.parser import ProductData

logger = logging.getLogger(__name__)


# ── Pin Result ────────────────────────────────────────────────────────────────

@dataclass
class PinResult:
    """Result of a pin creation attempt."""
    success:   bool
    pin_id:    str              = ""
    pin_url:   str              = ""
    board_id:  str              = ""
    board_name:str              = ""
    error:     Optional[str]    = None

    @property
    def pinterest_url(self) -> str:
        if self.pin_id:
            return f"https://www.pinterest.com/pin/{self.pin_id}/"
        return ""


# ══════════════════════════════════════════════════════════════════
# Description builders
# ══════════════════════════════════════════════════════════════════

def _build_product_description(
    title:    str,
    price:    Optional[str],
    rating:   Optional[float],
    reviews:  Optional[int],
    brand:    str = "",
    category: str = "",
) -> str:
    """Build engaging product pin description with hashtags."""
    lines = []

    # Main line with price
    if price:
        lines.append(f"💰 {title[:60]} — à partir de {price}€")
    else:
        lines.append(f"✨ {title[:70]}")

    # Rating line
    if rating and reviews:
        lines.append(f"⭐ {rating}/5 ({reviews:,} avis)")
    elif rating:
        lines.append(f"⭐ {rating}/5")

    # Brand
    if brand:
        lines.append(f"Marque: {brand}")

    # CTA
    lines.append("🛒 Voir sur Amazon → lien dans le profil")

    # Hashtags
    tags = ["#amazonfrance", "#bonplan", "#shopping"]
    if category:
        tag = "#" + category.lower().replace(" ", "").replace("é","e").replace("è","e")[:20]
        tags.append(tag)
    if brand:
        tags.append("#" + brand.lower().replace(" ","")[:15])

    description = "\n".join(lines) + "\n\n" + " ".join(tags[:5])
    return description[:500]


def _build_article_description(
    title:    str,
    excerpt:  str   = "",
    category: str   = "",
    read_time:int   = 0,
) -> str:
    """Build engaging article pin description."""
    lines = []

    lines.append(f"📖 {title[:70]}")

    if excerpt:
        lines.append(excerpt[:150] + ("..." if len(excerpt) > 150 else ""))

    info_parts = []
    if read_time:
        info_parts.append(f"⏱ {read_time} min de lecture")
    if category:
        info_parts.append(f"📌 {category}")
    if info_parts:
        lines.append(" | ".join(info_parts))

    lines.append("🔗 Lire l'article complet → lien dans bio")

    tags = ["#blog", "#guide", "#conseils", "#amazon"]
    if category:
        tags.append("#" + category.lower().replace(" ", "")[:15])

    description = "\n".join(lines) + "\n\n" + " ".join(tags[:5])
    return description[:500]


# ══════════════════════════════════════════════════════════════════
# Pin Pipeline
# ══════════════════════════════════════════════════════════════════

class PinCreator:
    """
    Creates Pinterest pins for different content types.

    Flow for each pin:
      1. Check daily cap (DailyScheduler)
      2. Get or create board (BoardManager)
      3. Compress image (TinyPNG)
      4. Build description
      5. Create pin via Pinterest API
      6. Record in daily log
      7. Update DB status

    Usage:
        creator = PinCreator()
        result = await creator.pin_product(product_data, affiliate_url="...")
    """

    def __init__(self):
        from pinterest.client import get_pinterest_client
        from pinterest.boards import BoardManager, DailyScheduler
        self._client    = get_pinterest_client()
        self._boards    = BoardManager()
        self._scheduler = DailyScheduler()

    async def pin_product(
        self,
        product:       "ProductData",
        affiliate_url: str           = "",
        force:         bool          = False,
    ) -> PinResult:
        """Create a product pin from ProductData."""
        if not self._client.is_configured:
            return PinResult(False, error="Pinterest not configured")

        # Daily cap check
        if not force and not await self._scheduler.can_pin_today():
            stats = await self._scheduler.get_stats()
            return PinResult(
                False,
                error=f"Daily cap reached ({stats['pins_today']}/{stats['daily_cap']})"
            )

        # Board
        board_id = await self._boards.get_or_create_board("product")
        if not board_id:
            return PinResult(False, error="Could not get/create board")

        # Image
        image_url = await self._get_compressed_image(product.image_url)
        if not image_url:
            return PinResult(False, error="No image URL available")

        # Link
        link = affiliate_url or product.affiliate_link or ""

        # Description
        description = _build_product_description(
            title=product.title,
            price=str(product.price) if product.price else None,
            rating=product.rating,
            reviews=product.reviews_count,
            brand=product.brand,
            category=product.category,
        )

        return await self._create_pin(
            board_id=board_id,
            title=product.title[:100],
            description=description,
            image_url=image_url,
            link=link,
            alt_text=f"{product.title} - Amazon",
            content_type="product",
            entity_id=product.asin,
        )

    async def pin_article(
        self,
        title:        str,
        image_url:    str,
        article_url:  str   = "",
        excerpt:      str   = "",
        category:     str   = "",
        read_time:    int   = 0,
        entity_id:    str   = "",
        force:        bool  = False,
    ) -> PinResult:
        """Create an article/guide pin."""
        if not self._client.is_configured:
            return PinResult(False, error="Pinterest not configured")

        if not force and not await self._scheduler.can_pin_today():
            return PinResult(False, error="Daily cap reached")

        board_id = await self._boards.get_or_create_board("article")
        if not board_id:
            return PinResult(False, error="Could not get/create board")

        comp_image = await self._get_compressed_image(image_url)
        if not comp_image:
            return PinResult(False, error="No image URL")

        description = _build_article_description(
            title=title,
            excerpt=excerpt,
            category=category,
            read_time=read_time,
        )

        return await self._create_pin(
            board_id=board_id,
            title=title[:100],
            description=description,
            image_url=comp_image,
            link=article_url,
            alt_text=title[:100],
            content_type="article",
            entity_id=entity_id or title[:50],
        )

    async def _create_pin(
        self,
        board_id:     str,
        title:        str,
        description:  str,
        image_url:    str,
        link:         str,
        alt_text:     str,
        content_type: str,
        entity_id:    str,
    ) -> PinResult:
        """Internal: create pin via API and record it."""
        try:
            logger.info(f"[pins] Creating {content_type} pin: {title[:50]}")
            pin_data = await self._client.create_pin(
                board_id=board_id,
                title=title,
                description=description,
                image_url=image_url,
                link=link,
                alt_text=alt_text,
            )
            pin_id = pin_data.get("id", "")

            # Record in daily log
            await self._scheduler.record_pin(
                content_type=content_type,
                entity_id=entity_id,
                pin_id=pin_id,
                board_id=board_id,
            )

            logger.info(f"[pins] ✅ Created pin: {pin_id}")
            return PinResult(
                success=True,
                pin_id=pin_id,
                pin_url=f"https://www.pinterest.com/pin/{pin_id}/",
                board_id=board_id,
            )

        except Exception as e:
            logger.error(f"[pins] ❌ Failed: {e}")
            return PinResult(False, error=str(e)[:200])

    async def _get_compressed_image(self, image_url: str) -> str:
        """Get TinyPNG compressed image URL (or original if compression fails)."""
        if not image_url:
            return ""
        try:
            from publishing.image import get_tinify_client
            tinify = get_tinify_client()
            result = await tinify.compress_from_url(image_url, download_result=False)
            return result.best_url
        except Exception:
            return image_url


# ══════════════════════════════════════════════════════════════════
# PHASE E — Multiple Variants (A/B/C Testing)
# ══════════════════════════════════════════════════════════════════

from dataclasses import dataclass as _dc, field as _field


@_dc
class PinVariant:
    """One pin variant with unique title + description + hashtags."""
    variant_id:   str           # "A", "B", "C"
    title:        str           # Max 100 chars
    description:  str           # Max 500 chars
    hashtags:     list          # 5-10 hashtags
    style:        str           # "listicle" | "quickfacts" | "story"

    @property
    def full_description(self) -> str:
        """Description + hashtags combined (max 500 chars)."""
        tags  = " ".join(self.hashtags[:8])
        combo = self.description.rstrip() + "\n\n" + tags
        return combo[:500]


@_dc
class VariantsResult:
    """Result of multi-variant pin creation."""
    variants_attempted: int            = 0
    variants_published: int            = 0
    results:            list           = _field(default_factory=list)
    cap_remaining:      int            = 0

    @property
    def success(self) -> bool:
        return self.variants_published > 0

    def summary(self) -> str:
        ok  = sum(1 for r in self.results if r.success)
        total = len(self.results)
        pins = [r.pinterest_url for r in self.results if r.success and r.pin_id]
        urls = "\n".join(f"  • {u}" for u in pins)
        return f"{ok}/{total} variants publiés\n{urls}"


# ── Variant builders ──────────────────────────────────────────────────────────

def _build_product_variant_A(title: str, price: str = "", rating: float = None) -> PinVariant:
    """Product Variant A — Deal/Price focus."""
    deal_title = f"🔥 Bon plan : {title[:65]}"
    lines = [f"💰 {title}"]
    if price:  lines.extend(["", f"💵 Prix : {price}€"])
    if rating: lines.append(f"⭐ Note : {rating}/5")
    lines.extend(["", "✅ Livraison rapide via Amazon",
                  "🛒 Voir l'offre → lien dans la bio"])
    hashtags = ["#bonplan","#amazon","#shopping","#deal","#promo",
                "#achatsmalin","#amazonfr","#reduction"]
    return PinVariant("A", deal_title[:100], "\n".join(lines), hashtags, "deal")


def _build_product_variant_B(title: str, brand: str = "", category: str = "") -> PinVariant:
    """Product Variant B — Review/Avis focus."""
    rev_title = f"Avis complet : {title[:65]}"
    lines = [f"📝 Test & Avis : {title}", ""]
    if brand: lines.append(f"Marque : {brand}")
    lines.extend(["✅ Pour qui est-il fait ?", "⚡ Points forts & faibles analysés",
                  "🎯 Notre verdict honnête", "", "🔗 Lire l'avis complet → lien bio"])
    hashtags = ["#avis","#test","#review","#amazon","#comparatif",
                "#achatsmalin","#shopping","#recommandation"]
    if category: hashtags.append(f"#{category.lower()[:12]}")
    return PinVariant("B", rev_title[:100], "\n".join(lines), hashtags, "review")


def _build_product_variant_C(title: str, category: str = "") -> PinVariant:
    """Product Variant C — Guide/Discovery focus."""
    guide_title = f"Le meilleur {category or 'produit'} : {title[:50]}"
    lines = [f"🏆 Pourquoi choisir {title[:50]} ?", ""]
    lines.extend(["💡 Guide d'achat complet", "✅ Meilleur rapport qualité/prix",
                  "📊 Comparé aux concurrents", "", "🔗 Guide complet → lien dans la bio"])
    hashtags = ["#guidachat","#meilleur","#amazon","#qualiteprix",
                "#shopping","#conseil","#recommendation"]
    if category: hashtags.append(f"#{category.lower()[:12]}")
    return PinVariant("C", guide_title[:100], "\n".join(lines), hashtags, "guide")


# ── PinVariantCreator ─────────────────────────────────────────────────────────

class PinVariantCreator(PinCreator):
    """
    Extended PinCreator that generates multiple variants per content.

    Usage:
        creator = PinVariantCreator()

        # 3 variants for a product
        result = await creator.pin_product_variants(product_data)
    """

    async def pin_product_variants(
        self,
        product,
        affiliate_url:  str   = "",
        max_variants:   int   = 3,
        force:          bool  = False,
    ) -> VariantsResult:
        """Create up to max_variants pins for a product."""
        vr = VariantsResult()

        remaining = await self._scheduler.get_remaining_today()
        vr.cap_remaining = remaining
        if remaining == 0 and not force:
            return vr

        price    = str(getattr(product,'price','')) if getattr(product,'price',None) else ''
        variants = [
            _build_product_variant_A(product.title, price, getattr(product,'rating',None)),
            _build_product_variant_B(product.title, getattr(product,'brand',''),
                                     getattr(product,'category','')),
            _build_product_variant_C(product.title, getattr(product,'category','')),
        ]

        image_url     = getattr(product,'image_url','')
        compressed_img= await self._get_compressed_image(image_url)
        board_id      = await self._boards.get_or_create_board("product")
        if not board_id:
            return vr

        link = affiliate_url or getattr(product,'affiliate_link','')
        for variant in variants[:max_variants]:
            if not force and not await self._scheduler.can_pin_today():
                break
            vr.variants_attempted += 1
            result = await self._create_pin(
                board_id=board_id,
                title=variant.title,
                description=variant.full_description,
                image_url=compressed_img or image_url,
                link=link,
                alt_text=f"{variant.style} - {product.title}",
                content_type="product",
                entity_id=f"{getattr(product,'asin','prod')[:15]}_v{variant.variant_id}",
            )
            vr.results.append(result)
            if result.success:
                vr.variants_published += 1
            if variant.variant_id != "C":
                import asyncio as _asyncio
                await _asyncio.sleep(2)

        vr.cap_remaining = await self._scheduler.get_remaining_today()
        return vr

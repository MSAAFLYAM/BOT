"""
ai/optimizer.py — SEO optimization and affiliate link injection.

Architecture decisions:
  - Optimization runs AFTER generation and scoring.
  - Affiliate links are injected POST-generation (not in the prompt).
    Reason: AI sometimes places links awkwardly in prompts. Post-injection
    is more reliable and controllable.
  - Meta description is generated from article content (first 160 chars of text).
  - Slug is generated from title (URL-friendly).
  - Keyword is added to title/H1 if missing (SEO requirement).
  - HTML is cleaned: remove double spaces, fix encoding issues.

Affiliate link strategy:
  - Replace {AFFILIATE_URL} placeholders with actual URL.
  - Add 1 link in the first third of article if none found.
  - Add 1 link before the conclusion.
  - Max 3 links total (Google penalty threshold).

Output:
  - OptimizedArticle dataclass with all SEO fields ready for publishing.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# ── Optimized Article ─────────────────────────────────────────────────────────

@dataclass
class OptimizedArticle:
    """
    A fully optimized article ready for publishing.

    Contains all fields needed for WordPress/Blogger/Telegram posts.
    """
    # Content
    html:             str     = ""
    title:            str     = ""
    meta_description: str     = ""
    slug:             str     = ""

    # SEO
    focus_keyword:    str     = ""
    tags:             list    = field(default_factory=list)
    category:         str     = ""

    # Affiliate
    affiliate_url:    str     = ""
    affiliate_count:  int     = 0

    # Publishing
    word_count:       int     = 0
    reading_time_min: int     = 0

    def to_dict(self) -> dict:
        return {
            "title":            self.title,
            "html":             self.html,
            "meta_description": self.meta_description,
            "slug":             self.slug,
            "focus_keyword":    self.focus_keyword,
            "tags":             self.tags,
            "category":         self.category,
            "affiliate_url":    self.affiliate_url,
            "word_count":       self.word_count,
            "reading_time_min": self.reading_time_min,
        }


# ── SEO Optimizer ─────────────────────────────────────────────────────────────

class SEOOptimizer:
    """
    Post-generation SEO optimization pipeline.

    Steps:
      1. Clean HTML (whitespace, encoding)
      2. Inject affiliate links (replace placeholders)
      3. Generate meta description
      4. Generate URL slug
      5. Ensure keyword in title
      6. Calculate word count and reading time

    Usage:
        optimizer = SEOOptimizer()
        article = optimizer.optimize(
            html=generated_html,
            title="Recette Tarte aux Pommes",
            keyword="tarte aux pommes",
            affiliate_url="https://amazon.fr/dp/B08XYZ?tag=xxx",
        )
        publish(article.html, article.meta_description, article.slug)
    """

    WORDS_PER_MINUTE = 200  # Average French reading speed

    def optimize(
        self,
        html:          str,
        title:         str,
        keyword:       str          = "",
        affiliate_url: str          = "",
        affiliate_urls:list[str]    = None,
        category:      str          = "",
        tags:          list[str]    = None,
    ) -> OptimizedArticle:
        """
        Run full optimization pipeline.

        Args:
            html:           Raw generated HTML
            title:          Article title
            keyword:        Primary SEO keyword
            affiliate_url:  Main affiliate URL (replaces {AFFILIATE_URL})
            affiliate_urls: Multiple URLs for comparisons ({AFFILIATE_URL_1}, etc.)
            category:       Article category
            tags:           Article tags

        Returns:
            OptimizedArticle ready for publishing.
        """
        # Step 1: Clean HTML
        clean = self._clean_html(html)

        # Step 2: Inject affiliate links
        clean, aff_count = self._inject_affiliate_links(
            clean, affiliate_url, affiliate_urls or []
        )

        # Step 3: Ensure keyword in title
        final_title = self._optimize_title(title, keyword)

        # Step 4: Generate meta description
        meta = self._generate_meta_description(clean, keyword)

        # Step 5: Generate slug
        slug = self._generate_slug(final_title)

        # Step 6: Word count & reading time
        text       = self._strip_html(clean)
        word_count = len(text.split())
        read_time  = max(1, round(word_count / self.WORDS_PER_MINUTE))

        # Step 7: Generate tags if not provided
        final_tags = tags or self._extract_tags(text, keyword)

        return OptimizedArticle(
            html=clean,
            title=final_title,
            meta_description=meta,
            slug=slug,
            focus_keyword=keyword,
            tags=final_tags,
            category=category,
            affiliate_url=affiliate_url,
            affiliate_count=aff_count,
            word_count=word_count,
            reading_time_min=read_time,
        )

    # ── HTML Cleaning ─────────────────────────────────────────────────────────

    def _clean_html(self, html: str) -> str:
        """Clean and normalize generated HTML."""
        if not html:
            return ""

        # Remove markdown code fences if AI wrapped in ```html
        html = re.sub(r"^```(?:html)?\s*", "", html.strip(), flags=re.M)
        html = re.sub(r"\s*```$", "", html.strip(), flags=re.M)

        # Remove unwanted wrapper tags
        for tag in ["<!DOCTYPE html>", "<html>", "</html>", "<body>", "</body>",
                    "<head>", "</head>"]:
            html = html.replace(tag, "")

        # Fix double spaces
        html = re.sub(r" {2,}", " ", html)

        # Fix double blank lines
        html = re.sub(r"\n{3,}", "\n\n", html)

        # Remove AI preamble/postamble (common artifacts)
        artifacts = [
            r"^Voici l'article.*?:\s*\n",
            r"^Here is the article.*?:\s*\n",
            r"^Article.*?:\s*\n",
            r"\nJ'espère que cet article.*$",
            r"\nNote:.*$",
        ]
        for pattern in artifacts:
            html = re.sub(pattern, "", html, flags=re.M | re.I)

        return html.strip()

    # ── Affiliate Link Injection ──────────────────────────────────────────────

    def _inject_affiliate_links(
        self,
        html:          str,
        main_url:      str,
        extra_urls:    list[str],
    ) -> tuple[str, int]:
        """
        Replace affiliate URL placeholders with actual URLs.

        Handles:
          {AFFILIATE_URL}   → main_url
          {AFFILIATE_URL_1} → extra_urls[0]
          {AFFILIATE_URL_2} → extra_urls[1]
          {AFFILIATE_URL_3} → extra_urls[2]

        If no placeholder found and main_url provided:
          → Add a CTA button before the conclusion.

        Returns (html, link_count).
        """
        count = 0

        # Replace numbered placeholders
        for i, url in enumerate(extra_urls[:3], 1):
            if url:
                placeholder = f"{{AFFILIATE_URL_{i}}}"
                if placeholder in html:
                    html = html.replace(
                        placeholder,
                        f'<a href="{url}" rel="nofollow sponsored" target="_blank">{url}</a>'
                    )
                    count += 1

        # Replace main placeholder
        if main_url and "{AFFILIATE_URL}" in html:
            html  = html.replace(
                "{AFFILIATE_URL}",
                f'<a href="{main_url}" rel="nofollow sponsored" target="_blank">{main_url}</a>'
            )
            count += 1

        # No placeholder found → inject CTA button before conclusion
        if count == 0 and main_url:
            cta = (
                f'\n\n<div class="affiliate-cta" style="text-align:center;margin:20px 0;">'
                f'<a href="{main_url}" rel="nofollow sponsored" target="_blank" '
                f'style="background:#FF9900;color:#fff;padding:12px 24px;border-radius:4px;'
                f'text-decoration:none;font-weight:bold;">🛒 Voir sur Amazon</a></div>\n\n'
            )
            # Insert before last H2 (typically conclusion)
            last_h2 = html.rfind("<h2")
            if last_h2 > 0:
                html = html[:last_h2] + cta + html[last_h2:]
                count = 1
            else:
                html = html + cta
                count = 1

        return html, count

    # ── Title Optimization ────────────────────────────────────────────────────

    def _optimize_title(self, title: str, keyword: str) -> str:
        """Ensure keyword appears in title for SEO."""
        if not title:
            return keyword or "Article"
        if not keyword:
            return title
        kw_lower = keyword.lower()
        if kw_lower in title.lower():
            return title
        # Keyword not in title → prepend keyword
        return f"{keyword.title()} : {title}"

    # ── Meta Description ──────────────────────────────────────────────────────

    def _generate_meta_description(self, html: str, keyword: str = "") -> str:
        """
        Generate meta description from article content.

        Takes the first meaningful paragraph (150-160 chars).
        Ensures keyword appears in meta description.
        """
        text = self._strip_html(html)
        sentences = [s.strip() for s in re.split(r'[.!?]', text) if len(s.strip()) > 30]

        if not sentences:
            return keyword or ""

        # Find a sentence containing the keyword
        if keyword:
            for sentence in sentences[:5]:
                if keyword.lower() in sentence.lower():
                    meta = sentence.strip()
                    return meta[:157] + "..." if len(meta) > 160 else meta

        # Use first good sentence
        meta = sentences[0].strip()
        return meta[:157] + "..." if len(meta) > 160 else meta

    # ── Slug Generation ───────────────────────────────────────────────────────

    def _generate_slug(self, title: str) -> str:
        """
        Generate URL-friendly slug from title.

        "Recette Tarte aux Pommes Classique" → "recette-tarte-aux-pommes-classique"
        """
        if not title:
            return "article"

        # Normalize unicode (é → e, à → a, etc.)
        normalized = unicodedata.normalize("NFD", title)
        ascii_str  = normalized.encode("ascii", "ignore").decode("ascii")

        # Lowercase, replace spaces/special chars with hyphens
        slug = ascii_str.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        slug = slug.strip("-")

        # Limit length (WordPress/Blogger limit ~75 chars)
        return slug[:75]

    # ── Tag Extraction ────────────────────────────────────────────────────────

    def _extract_tags(self, text: str, keyword: str = "") -> list[str]:
        """Extract relevant tags from article text."""
        tags = []
        if keyword:
            tags.extend(keyword.lower().split())

        # Common French product/food tags
        FOOD_TAGS = [
            "recette", "cuisine", "gateau", "soupe", "salade", "poulet",
            "chocolat", "facile", "rapide", "végétarien", "dessert",
            "entrée", "plat principal", "végétalien", "sans gluten",
        ]
        text_lower = text.lower()
        for tag in FOOD_TAGS:
            if tag in text_lower and tag not in tags:
                tags.append(tag)

        # Amazon/product tags
        PRODUCT_TAGS = [
            "amazon", "avis", "test", "comparatif", "meilleur",
            "guide achat", "pas cher", "qualité prix",
        ]
        for tag in PRODUCT_TAGS:
            if tag in text_lower and tag not in tags:
                tags.append(tag)

        return list(set(tags))[:10]

    # ── Strip HTML ────────────────────────────────────────────────────────────

    def _strip_html(self, html: str) -> str:
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'&[a-z]+;', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

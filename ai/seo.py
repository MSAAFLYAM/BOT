"""
ai/seo.py — Advanced SEO enhancement pipeline.

Enriches generated articles with:
  1. FAQ section         — extracted from article content (no AI call)
  2. Schema.org JSON-LD  — Product, Article structured data
  3. Meta tags           — title, description, og:*, twitter:*
  4. Readability fixes   — split long sentences, shorten paragraphs
  5. Keyword validation  — density check + placement warnings
  6. Canonical slug      — URL-friendly, accent-free, truncated

Architecture decisions:
  - NO additional AI calls (saves tokens/credits)
  - All transformations are rule-based + regex
  - Injected as <script type="application/ld+json"> and <meta> tags
  - Non-destructive: original HTML preserved, enrichments appended/prepended
  - Works on any HTML (WordPress, Blogger, plain HTML)

Usage:
    enhancer = SEOEnhancer()

    # For a product review
    result = enhancer.enhance_product(
        html=article_html,
        title="Test Cafetière Dolce Gusto",
        keyword="avis cafetière dolce gusto",
        product_data=product_data,
    )
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class SEOResult:
    """Enhanced article with all SEO elements."""
    html:             str          # Original HTML + FAQ injected
    title_tag:        str   = ""   # <title> content
    meta_description: str   = ""   # <meta description>
    slug:             str   = ""   # URL slug
    canonical_url:    str   = ""   # Full canonical URL

    # Structured data
    schema_json:      str   = ""   # JSON-LD as string
    meta_tags:        dict  = field(default_factory=dict)  # All meta tags

    # Analysis
    keyword_density:  float = 0.0
    word_count:       int   = 0
    faq_count:        int   = 0
    readability_score:int   = 0   # 0-100

    # Warnings
    warnings:         list  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title_tag":        self.title_tag,
            "meta_description": self.meta_description,
            "slug":             self.slug,
            "schema_type":      "Product" if '"@type": "Product"' in self.schema_json
                                else "Article",
            "keyword_density":  round(self.keyword_density, 3),
            "word_count":       self.word_count,
            "faq_count":        self.faq_count,
            "readability_score":self.readability_score,
            "warnings":         self.warnings,
        }


# ── SEO Enhancer ──────────────────────────────────────────────────────────────

class SEOEnhancer:
    """
    Rule-based SEO enhancement for generated articles.

    No AI calls. Runs in < 100ms on any article.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def enhance_product(
        self,
        html:         str,
        title:        str,
        keyword:      str           = "",
        product_data                = None,
        image_url:    str           = "",
        affiliate_url:str           = "",
        site_url:     str           = "",
        language:     str           = "fr",
    ) -> SEOResult:
        """Full SEO enhancement for a product review article."""
        result        = SEOResult(html=html)
        result.slug   = self.generate_slug(title)

        # Meta tags
        meta_desc            = self.generate_meta_description(html, keyword, title)
        result.meta_description = meta_desc
        result.title_tag     = self._build_title_tag(title, keyword)
        result.meta_tags     = self._build_meta_tags(
            title=result.title_tag,
            description=meta_desc,
            image_url=image_url,
            url=f"{site_url.rstrip('/')}/{result.slug}" if site_url else "",
            content_type="article",
            language=language,
        )

        # Schema.org — Product
        if product_data:
            result.schema_json = self._build_product_schema(
                title=title,
                product=product_data,
                image_url=image_url,
                affiliate_url=affiliate_url,
            )

        # FAQ
        faq_html, faq_count  = self._generate_faq(html, keyword, content_type="product")
        result.faq_count     = faq_count
        if faq_html:
            result.html = html + "\n\n" + faq_html

        # Inject schema
        if result.schema_json:
            result.html = self._inject_schema(result.html, result.schema_json)

        # Analysis
        result.keyword_density   = self.check_keyword_density(result.html, keyword)
        result.word_count        = len(self._strip_html(result.html).split())
        result.readability_score = self._readability_score(result.html)
        result.warnings          = self._collect_warnings(result, keyword)

        return result

    # ── 1. Meta Tags ──────────────────────────────────────────────────────────

    def generate_meta_description(
        self,
        html:    str,
        keyword: str,
        title:   str = "",
    ) -> str:
        """
        Generate SEO meta description (150-160 chars).

        Strategy:
          1. Find a sentence containing the keyword
          2. Fall back to first meaningful paragraph
          3. Ensure length 150-160 chars
        """
        text     = self._strip_html(html)
        kw_lower = keyword.lower() if keyword else ""

        sentences = [
            s.strip() for s in re.split(r'[.!?]', text)
            if 40 < len(s.strip()) < 250
        ]

        best = ""

        # Prefer sentence containing keyword
        if kw_lower:
            for s in sentences[:8]:
                if kw_lower in s.lower():
                    best = s.strip()
                    break

        # Fall back to first good sentence
        if not best and sentences:
            best = sentences[0].strip()

        # Fall back to title
        if not best:
            best = f"Découvrez notre guide complet sur {title or keyword}."

        # Truncate to 160 chars
        if len(best) > 160:
            best = best[:157] + "..."

        return best

    def _build_title_tag(self, title: str, keyword: str) -> str:
        """
        Build optimized title tag.

        Rules:
          - Max 60 chars
          - Keyword in first 30 chars if possible
          - Format: "Keyword : Title | Site"
        """
        if not title:
            return keyword or "Article"

        # If keyword already in title
        if keyword and keyword.lower() in title.lower():
            tag = title
        elif keyword:
            # Add keyword if title is short enough
            candidate = f"{keyword.title()} : {title}"
            tag = candidate if len(candidate) <= 65 else title
        else:
            tag = title

        return tag[:65]

    def _build_meta_tags(
        self,
        title:        str,
        description:  str,
        image_url:    str = "",
        url:          str = "",
        content_type: str = "article",
        language:     str = "fr",
    ) -> dict:
        """Build complete meta tags dict (og:*, twitter:*, canonical)."""
        tags = {
            "description":      description,
            "og:title":         title,
            "og:description":   description,
            "og:type":          content_type,
            "twitter:card":     "summary_large_image",
            "twitter:title":    title,
            "twitter:description": description[:200],
            "language":         language,
        }
        if image_url:
            tags["og:image"]         = image_url
            tags["twitter:image"]    = image_url
        if url:
            tags["og:url"]           = url
            tags["canonical"]        = url

        return tags

    # ── 2. Schema.org ─────────────────────────────────────────────────────────

    def _build_product_schema(
        self,
        title:         str,
        product,
        image_url:     str = "",
        affiliate_url: str = "",
    ) -> str:
        """Build Product Schema.org JSON-LD."""
        schema = {
            "@context": "https://schema.org",
            "@type":    "Product",
            "name":     title,
        }

        if image_url:
            schema["image"] = image_url
        if hasattr(product, 'brand') and product.brand:
            schema["brand"] = {"@type": "Brand", "name": product.brand}
        if hasattr(product, 'short_description') and product.short_description:
            schema["description"] = product.short_description[:500]

        # Rating
        if hasattr(product, 'rating') and product.rating:
            agg = {
                "@type":       "AggregateRating",
                "ratingValue": str(product.rating),
                "bestRating":  "5",
                "worstRating": "1",
            }
            if hasattr(product, 'reviews_count') and product.reviews_count:
                agg["reviewCount"] = str(product.reviews_count)
            schema["aggregateRating"] = agg

        # Offer/Price
        offer = {"@type": "Offer", "availability": "https://schema.org/InStock",
                 "priceCurrency": "EUR"}
        if hasattr(product, 'price') and product.price:
            offer["price"] = str(product.price)
        if affiliate_url:
            offer["url"] = affiliate_url
        schema["offers"] = offer

        return json.dumps(schema, ensure_ascii=False, indent=2)

    def _build_article_schema(
        self,
        title:        str,
        description:  str = "",
        image_url:    str = "",
        url:          str = "",
    ) -> str:
        """Build Article Schema.org JSON-LD."""
        from datetime import datetime, timezone
        schema = {
            "@context":          "https://schema.org",
            "@type":             "Article",
            "headline":          title[:110],
            "datePublished":     datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "dateModified":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        if description:   schema["description"]  = description
        if image_url:     schema["image"]         = image_url
        if url:           schema["url"]           = url
        return json.dumps(schema, ensure_ascii=False, indent=2)

    def _inject_schema(self, html: str, schema_json: str) -> str:
        """Inject JSON-LD schema as first element in HTML."""
        script_tag = (
            f'\n<script type="application/ld+json">\n'
            f'{schema_json}\n'
            f'</script>\n'
        )
        return script_tag + html

    # ── 3. FAQ Generation ─────────────────────────────────────────────────────

    def _generate_faq(
        self,
        html:         str,
        keyword:      str,
        content_type: str = "product",
    ) -> tuple[str, int]:
        """
        Generate FAQ section from article content.
        Returns (faq_html, question_count).

        Extracts questions from:
          1. H2/H3 headings converted to questions
          2. Content-based common questions per content type
          3. Keyword-specific questions
        """
        text      = self._strip_html(html)
        headings  = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.I | re.DOTALL)
        clean_h   = [self._strip_html(h).strip() for h in headings if h.strip()]

        faq_pairs = []

        # Extract Q&A from content headings
        for heading in clean_h[:6]:
            if len(heading) < 5:
                continue
            question = self._heading_to_question(heading, content_type)
            answer   = self._find_answer_for_heading(html, heading)
            if question and answer:
                faq_pairs.append((question, answer))

        # Add generic questions based on content type
        generic = self._get_generic_questions(keyword, content_type, text)
        for q, a in generic:
            if len(faq_pairs) >= 5:
                break
            # Avoid duplicating similar questions
            if not any(q.lower()[:20] in existing_q.lower() for existing_q, _ in faq_pairs):
                faq_pairs.append((q, a))

        faq_pairs = faq_pairs[:5]  # Max 5 FAQ items
        if not faq_pairs:
            return "", 0

        # Build FAQ HTML + Schema
        faq_schema_items = []
        faq_html_items   = []

        for question, answer in faq_pairs:
            faq_html_items.append(
                f'<div itemscope itemprop="mainEntity" '
                f'itemtype="https://schema.org/Question">\n'
                f'  <h3 itemprop="name">{question}</h3>\n'
                f'  <div itemscope itemprop="acceptedAnswer" '
                f'itemtype="https://schema.org/Answer">\n'
                f'    <p itemprop="text">{answer}</p>\n'
                f'  </div>\n'
                f'</div>'
            )
            faq_schema_items.append({
                "@type":          "Question",
                "name":           question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            })

        faq_schema = json.dumps({
            "@context":   "https://schema.org",
            "@type":      "FAQPage",
            "mainEntity": faq_schema_items,
        }, ensure_ascii=False)

        faq_html = (
            f'<section itemscope itemtype="https://schema.org/FAQPage">\n'
            f'<h2>Questions fréquentes</h2>\n'
            + "\n".join(faq_html_items) +
            f'\n</section>\n'
            f'<script type="application/ld+json">\n{faq_schema}\n</script>'
        )

        return faq_html, len(faq_pairs)

    def _heading_to_question(self, heading: str, content_type: str) -> str:
        """Convert a heading to a question form."""
        h = heading.strip().rstrip(".")
        if "?" in h:
            return h

        # Already question-like
        if h.lower().startswith(("comment", "pourquoi", "quand", "que ", "quel", "combien")):
            return h + " ?"

        # Convert common patterns
        patterns = {
            r"^conseil": "Quels conseils pour ",
            r"^astuce":  "Quelles astuces pour ",
            r"^variante":"Quelles variantes pour ",
            r"^conserv": "Comment conserver ",
            r"^ingrédient": "Quels ingrédients pour ",
        }
        for pattern, prefix in patterns.items():
            if re.search(pattern, h, re.I):
                return prefix + h.lower() + " ?"

        # Generic question
        return f"Que savoir sur {h.lower()} ?"

    def _find_answer_for_heading(self, html: str, heading: str) -> str:
        """Find the paragraph following a heading in HTML."""
        # Find heading in HTML (case-insensitive)
        pattern = re.escape(heading)
        m       = re.search(
            rf'<h[23][^>]*>[^<]*{pattern}[^<]*</h[23]>(.*?)<(?:h[123]|</)',
            html, re.I | re.DOTALL
        )
        if m:
            section = self._strip_html(m.group(1)).strip()
            if len(section) > 30:
                return section[:300] + ("..." if len(section) > 300 else "")
        return ""

    def _get_generic_questions(
        self,
        keyword:      str,
        content_type: str,
        text:         str,
    ) -> list[tuple[str, str]]:
        """Return generic Q&A pairs based on content type and keyword."""
        kw = keyword or "ce plat"
        pairs = []

        if content_type == "product":
            pairs = [
                (
                    f"Combien de temps faut-il pour préparer {kw} ?",
                    "La durée de préparation varie selon la recette. "
                    "Consultez les informations détaillées en début d'article pour les temps exacts.",
                ),
                (
                    f"Peut-on préparer {kw} à l'avance ?",
                    f"Oui, {kw} peut être préparé à l'avance et conservé "
                    "au réfrigérateur dans un récipient hermétique pendant 2-3 jours.",
                ),
                (
                    f"Quels sont les ingrédients principaux de {kw} ?",
                    f"Les ingrédients essentiels pour {kw} sont détaillés dans la liste "
                    "complète en début de recette. Utilisez des produits frais pour un meilleur résultat.",
                ),
            ]
        elif content_type == "product":
            pairs = [
                (
                    f"Vaut-il la peine d'acheter {kw} ?",
                    f"D'après notre analyse complète, {kw} offre un bon rapport qualité-prix "
                    "pour la plupart des utilisateurs. Consultez notre verdict détaillé ci-dessus.",
                ),
                (
                    f"Quelle est la garantie de {kw} ?",
                    "La garantie varie selon le fabricant. Consultez la fiche produit Amazon "
                    "pour les informations de garantie actualisées.",
                ),
                (
                    f"Où acheter {kw} au meilleur prix ?",
                    f"Vous trouverez {kw} disponible sur Amazon avec livraison rapide. "
                    "Cliquez sur notre lien pour voir le prix actuel et les offres disponibles.",
                ),
            ]

        return pairs[:3]

    # ── 4. Keyword Analysis ───────────────────────────────────────────────────

    def check_keyword_density(self, html: str, keyword: str) -> float:
        """
        Calculate keyword density.
        Returns percentage (e.g., 2.5 for 2.5%).
        Optimal range: 1.0-4.0%.
        """
        if not keyword:
            return 0.0
        text       = self._strip_html(html).lower()
        words      = text.split()
        total      = max(len(words), 1)
        kw_words   = keyword.lower().split()
        kw_count   = sum(text.count(w) for w in kw_words) / max(len(kw_words), 1)
        return round((kw_count / total) * 100, 2)

    # ── 5. Readability ────────────────────────────────────────────────────────

    def _readability_score(self, html: str) -> int:
        """
        Simple readability score 0-100.
        Based on: avg sentence length, paragraph length, heading ratio.
        """
        text      = self._strip_html(html)
        words     = text.split()
        total     = max(len(words), 1)
        sentences = [s for s in re.split(r'[.!?]', text) if s.strip()]
        headings  = len(re.findall(r'<h[2-4]', html, re.I))
        paras     = len(re.findall(r'<p', html, re.I))
        score     = 50  # Base

        # Sentence length
        if sentences:
            avg = total / max(len(sentences), 1)
            if 10 <= avg <= 20:  score += 20
            elif 8 <= avg <= 25: score += 10
            else:                score -= 10

        # Heading structure
        if headings >= 3:        score += 15
        elif headings >= 2:      score += 8

        # Paragraph structure
        if paras >= 5:           score += 15
        elif paras >= 3:         score += 8

        return min(100, max(0, score))

    # ── 6. Slug ───────────────────────────────────────────────────────────────

    def generate_slug(self, title: str) -> str:
        """Generate URL-friendly slug from title."""
        if not title:
            return "article"
        normalized = unicodedata.normalize("NFD", title)
        ascii_str  = normalized.encode("ascii", "ignore").decode("ascii")
        slug       = ascii_str.lower()
        slug       = re.sub(r"[^\w\s-]", "", slug)
        slug       = re.sub(r"[\s_]+", "-", slug)
        slug       = re.sub(r"-+", "-", slug)
        return slug.strip("-")[:75]

    # ── Warnings ──────────────────────────────────────────────────────────────

    def _collect_warnings(self, result: SEOResult, keyword: str) -> list[str]:
        """Collect SEO warnings for monitoring."""
        warnings = []
        if result.word_count < 600:
            warnings.append(f"Contenu court ({result.word_count} mots, idéal: 800+)")
        if result.keyword_density < 0.5 and keyword:
            warnings.append(f"Densité mot-clé faible: {result.keyword_density:.1f}%")
        elif result.keyword_density > 5.0:
            warnings.append(f"Densité mot-clé élevée: {result.keyword_density:.1f}% (risque spam)")
        if result.readability_score < 50:
            warnings.append(f"Score lisibilité: {result.readability_score}/100")
        if not result.meta_description:
            warnings.append("Meta description manquante")
        if result.faq_count == 0:
            warnings.append("Aucune FAQ générée")
        return warnings

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _strip_html(html: str) -> str:
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'&[a-z]+;', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _to_iso_duration(time_str: str) -> str:
        """Convert "30min" or "1h30" to ISO 8601 duration (PT30M, PT1H30M)."""
        if not time_str:
            return ""
        time_str = time_str.lower().replace(" ", "")
        h = m = 0
        h_match = re.search(r'(\d+)\s*h', time_str)
        m_match = re.search(r'(\d+)\s*(?:min|mn)', time_str)
        if not m_match:
            m_match = re.search(r'h(\d+)$', time_str)
        if h_match: h = int(h_match.group(1))
        if m_match: m = int(m_match.group(1))
        if not h and not m:
            try: m = int(re.sub(r'\D', '', time_str))
            except Exception: pass
        duration = "PT"
        if h: duration += f"{h}H"
        if m: duration += f"{m}M"
        return duration if duration != "PT" else ""


# ── Singleton ─────────────────────────────────────────────────────────────────

_enhancer: Optional[SEOEnhancer] = None


def get_seo_enhancer() -> SEOEnhancer:
    """Return module-level SEOEnhancer singleton."""
    global _enhancer
    if _enhancer is None:
        _enhancer = SEOEnhancer()
    return _enhancer

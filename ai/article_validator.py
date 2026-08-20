"""
ai/article_validator.py
━━━━━━━━━━━━━━━━━━━━━━
Valide la qualité d'un article avant publication:
  ✅ Longueur minimale (1500+ mots)
  ✅ Structure HTML (h1, h2)
  ✅ Images présentes
  ✅ Schema.org JSON-LD
  ✅ CTA button
  ✅ Print button
  ✅ Score qualité /100
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    passed:         bool
    score:          int
    word_count:     int
    issues:         list[str] = field(default_factory=list)
    warnings:       list[str] = field(default_factory=list)
    passed_checks:  list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        lines = [
            f"{status} — Score: {self.score}/100",
            f"Words: {self.word_count}",
        ]
        if self.issues:
            lines.append(f"Issues ({len(self.issues)}):")
            for i in self.issues: lines.append(f"  ❌ {i}")
        if self.warnings:
            for w in self.warnings: lines.append(f"  ⚠️ {w}")
        return "\n".join(lines)


def validate_article(html: str, title: str = "") -> ValidationResult:
    """
    Validate article HTML quality before publishing.
    Returns ValidationResult with score and issues.
    """
    score  = 0
    issues = []
    warns  = []
    passed = []

    # ── Word count ────────────────────────────────────────────────────────
    clean_text = re.sub(r"<[^>]+>", " ", html)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    words = len(clean_text.split())

    if words >= 1500:
        score += 20; passed.append(f"Word count: {words} words")
    elif words >= 1000:
        score += 12; warns.append(f"Article is {words} words. Aim for 1500+")
    elif words >= 700:
        score += 6; warns.append(f"Article is short: {words} words")
    else:
        issues.append(f"Too short: {words} words (min 700)")

    # ── H1 ────────────────────────────────────────────────────────────────
    h1_count = len(re.findall(r"<h1[^>]*>", html, re.I))
    if h1_count == 1:
        score += 10; passed.append("H1 present (1)")
    elif h1_count == 0:
        issues.append("Missing H1 tag")
    else:
        warns.append(f"Multiple H1 tags ({h1_count}) — keep only 1")
        score += 5

    # ── H2 sections ───────────────────────────────────────────────────────
    h2_count = len(re.findall(r"<h2[^>]*>", html, re.I))
    if h2_count >= 5:
        score += 10; passed.append(f"H2 sections: {h2_count}")
    elif h2_count >= 3:
        score += 6; warns.append(f"Only {h2_count} H2 sections. Aim for 5+")
    else:
        issues.append(f"Too few H2 sections: {h2_count}")

    # ── Images ────────────────────────────────────────────────────────────
    img_count = len(re.findall(r"<img[^>]+>", html, re.I))
    if img_count >= 4:
        score += 8; passed.append(f"Images: {img_count}")
    elif img_count >= 2:
        score += 5; warns.append(f"Only {img_count} images. Aim for 4+")
    elif img_count == 1:
        score += 2; warns.append("Only 1 image (hero). Add step photos")
    else:
        issues.append("No images found")

    # ── Schema.org ────────────────────────────────────────────────────────
    has_schema = bool(re.search(r'application/ld\+json.*?"@type":\s*"Product"', html, re.I | re.DOTALL))
    if has_schema:
        score += 8; passed.append("Schema.org Product JSON-LD")
    else:
        warns.append("Missing Schema.org Product markup")

    # ── FAQ ───────────────────────────────────────────────────────────────
    faq_count = len(re.findall(r"<details[^>]*>", html, re.I))
    if faq_count >= 4:
        score += 6; passed.append(f"FAQ: {faq_count} questions")
    elif faq_count >= 2:
        score += 3; warns.append(f"Only {faq_count} FAQ items. Aim for 5")
    else:
        warns.append("No FAQ section (adds +5 SEO score)")

    # ── CTA ───────────────────────────────────────────────────────────────
    has_cta = bool(re.search(r'cta-btn|cta-section|View Full Review|Buy Now', html, re.I))
    if has_cta:
        score += 6; passed.append("CTA button present")
    else:
        warns.append("No CTA button")

    # ── Print button ──────────────────────────────────────────────────────
    has_print = bool(re.search(r'print-btn|printRecipe\(\)', html, re.I))
    if has_print:
        score += 4; passed.append("Print button present")
    else:
        warns.append("No print button")

    # ── Nutrition ─────────────────────────────────────────────────────────
    has_nutrition = bool(re.search(r'nutrition|calories|Calories', html, re.I))
    if has_nutrition:
        score += 2; passed.append("Nutrition info present")

    # ── Final ─────────────────────────────────────────────────────────────
    score   = min(score, 100)
    passed_ = score >= 65

    return ValidationResult(
        passed        = passed_,
        score         = score,
        word_count    = words,
        issues        = issues,
        warnings      = warns,
        passed_checks = passed,
    )


def validate_and_log(html: str, title: str = "") -> ValidationResult:
    """Validate + log result."""
    import logging
    logger = logging.getLogger(__name__)
    result = validate_article(html, title)
    if result.passed:
        logger.info(f"[validator] ✅ {title[:40]} — {result.score}/100 ({result.word_count}w)")
    else:
        logger.warning(f"[validator] ❌ {title[:40]} — {result.score}/100 | {result.issues}")
    return result

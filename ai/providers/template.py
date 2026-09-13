"""
ai/providers/template.py — Pure Python template-based article generator.

No API, no credits, no rate limits. ALWAYS works.
Uses product data to fill structured HTML templates.

Quality: lower than AI-generated, but:
  - Correct structure (H2/H3, lists, conclusion)
  - SEO-optimized (keyword in title, headings, density ~2%)
  - Ready to publish immediately
  - Passes quality scorer (score ~55-65/100)

Used as LAST RESORT when all AI providers fail.
"""
from __future__ import annotations

import logging
import re
import html as _html
from typing import Optional

from ai.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger(__name__)


class TemplateProvider(BaseProvider):
    """
    Pure Python template generator.
    Always available. No API key needed.
    """
    name  = "template"
    model = "python-template-v1"

    def is_available(self) -> bool:
        return True  # Always available

    async def generate(
        self,
        prompt:     str,
        max_tokens: int   = 2000,
        temperature:float = 0.7,
    ) -> ProviderResult:
        """
        Extract data from the prompt and fill an HTML template.
        The prompt contains product info in structured format.
        """
        try:
            html = self._generate_from_prompt(prompt)
            logger.info(f"[template] Generated {len(html.split())} words via template")
            return ProviderResult(
                text=html, provider=self.name, model=self.model,
                tokens=len(html.split()), success=True,
            )
        except Exception as e:
            logger.error(f"[template] Failed: {e}")
            return ProviderResult("", self.name, self.model, success=False,
                                  error=str(e)[:100])

    def _generate_from_prompt(self, prompt: str) -> str:
        """Extract structured data from prompt and generate HTML article."""

        # Extract key data from prompt text
        title        = self._extract_field(prompt, "PRODUCT|TITLE|article", 80)
        features     = self._extract_list(prompt, "FEATURES|HIGHLIGHTS|KEY POINTS")
        steps        = self._extract_list(prompt, "STEPS|INSTRUCTIONS|REVIEW")
        price        = self._extract_field(prompt, "Price|Prix", 20)
        rating       = self._extract_field(prompt, "Rating|Note|Stars", 20)
        brand        = self._extract_field(prompt, "Brand|Marque", 20)

        if not title:
            # Try to get title from first line
            lines = [l.strip() for l in prompt.split('\n') if l.strip()]
            title = lines[1] if len(lines) > 1 else "Product Review"

        return self._product_template(
            title=title,
            features=features,
            steps=steps,
            price=price,
            rating=rating,
            brand=brand,
        )

    def _product_template(
        self,
        title:       str,
        features:    list,
        steps:       list,
        price:       str = "",
        rating:      str = "",
        brand:       str = "",
    ) -> str:
        """Generate a complete HTML product review article (v4.2 theme structure, no inline CSS)."""
        import html as _h

        def _t(s) -> str:
            t = _h.unescape(str(s or ""))
            for _ in range(3):
                u = _h.unescape(t)
                if u == t:
                    break
                t = u
            return _h.escape(t, quote=True)

        title = _t(title or "Product Review")
        feats = [_t(f) for f in (features or []) if str(f).strip()]
        if not feats and steps:
            feats = [_t(s) for s in steps[:4] if str(s).strip()]

        # ── Verdict summary ──
        intro = f"""<p class="verdict-summary">The {title} is a solid choice that delivers on its promises. It balances quality features with an accessible price point.</p>"""

        # ── Key features + analysis (v4.2) ──
        feat_html = ""
        if feats:
            _items = "".join([f"<li>{f[:120]}</li>" for f in feats[:6]])
            feat_html = f"<h2>Key Features</h2><ul>{_items}</ul>"
        _steps = [_t(s) for s in (steps or [])[:6] if len(str(s).strip()) > 5]
        if _steps:
            _sp = " ".join(_steps)[:600]
        else:
            _sp = f"Based on specifications and user feedback for the {title[:60]}, it offers solid build quality and practical features for its price tier."
        steps_html = f"<h2>In-Depth Analysis</h2><p>{_sp}</p>"

        # ── Pros and Cons (v4.2) ──
        pros_cons_html = """<div class="pros-cons-grid">
<div class="pros-box"><h4><i class="fas fa-check-circle"></i> Pros</h4><ul><li>Good value for the price</li><li>Solid build quality</li><li>Positive user feedback</li></ul></div>
<div class="cons-box"><h4><i class="fas fa-times-circle"></i> Cons</h4><ul><li>Availability may vary</li><li>Check compatibility with your setup</li></ul></div>
</div>"""

        # ── Who is this for (v4.2) ──
        who_html = """<h2>Who Is This For?</h2><ul><li>Value-conscious shoppers comparing alternatives</li><li>Users who need core features without premium cost</li></ul><h3>Who Should Skip This?</h3><ul><li>Buyers needing ultra-premium niche features</li></ul>"""

        # ── Specs Table (v4.2, real data only — no generic placeholders) ──
        specs_html = ""
        if feats:
            rows = "".join(
                f"<tr><td>Feature {i+1}</td><td>{f[:100]}</td></tr>"
                for i, f in enumerate(feats[:4])
            )
            specs_html = f"<h2>Key Specifications</h2><table class=\"spec-table\"><tbody>{rows}</tbody></table>"

        # ── FAQ (v4.2, native details) ──
        faq_html = """<h2>Frequently Asked Questions</h2>
<details open><summary>Is this worth the investment?</summary><p>It offers good value if the features match your use case — compare with alternatives before deciding.</p></details>
<details><summary>What should I check before buying?</summary><p>Verify compatibility, dimensions, and recent reviews for your specific setup.</p></details>"""

        # ── Final Verdict (v4.2) ──
        verdict_html = f"""<h2>Final Verdict</h2>
<p>The {title[:60]} is a solid choice that delivers on its promises. Check the current availability and secure the best price if it fits your setup.</p>"""

        # ── Assemble ──
        html = "\n\n".join([
            intro,
            feat_html,
            pros_cons_html,
            steps_html,
            who_html,
            specs_html,
            faq_html,
            verdict_html,
        ])

        return _html.unescape(html)

    # ── Extraction helpers ─────────────────────────────────────────────────────

    def _extract_field(self, text: str, label: str, max_len: int = 100) -> str:
        """Extract a field value from prompt text."""
        pattern = rf"(?:{label})\s*[:\-]?\s*([^\n]{{1,{max_len}}})"
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1).strip()
        return ""

    def _extract_list(self, text: str, label: str) -> list:
        """Extract a list (ingredients/steps) from prompt text."""
        # Find the section
        pattern = rf"(?:{label})[^\n]*\n((?:.*\n){{1,30}})"
        m = re.search(pattern, text, re.I)
        if not m:
            return []

        section = m.group(1)
        items   = []
        for line in section.split('\n'):
            line = line.strip()
            # Remove list markers
            line = re.sub(r'^[-•*\d]+[\.\)]\s*', '', line)
            if 3 < len(line) < 300:
                items.append(line)
        return items[:20]

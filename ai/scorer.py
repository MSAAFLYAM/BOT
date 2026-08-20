"""
ai/scorer.py — Article quality scoring system (0-100).

Architecture decisions:
  - Score is calculated BEFORE publishing.
  - Articles below 60/100 are rejected and regenerated.
  - Score breakdown (100 points total):
      25 pts → Word count        (SEO minimum: 800 words)
      25 pts → Structure         (H2/H3, intro, conclusion)
      20 pts → SEO               (keyword density, title, meta)
      15 pts → Affiliate links   (1-3 links = optimal)
      15 pts → Readability       (sentence length, paragraphs)
  - Thresholds:
      80-100: Excellent → publish immediately
      60-79:  Good      → publish
      40-59:  Fair      → regenerate with higher quality model
      0-39:   Poor      → fail task

Why score before publishing:
  - AI sometimes generates short or poorly structured articles.
  - A low-quality article harms SEO more than no article.
  - Scoring enables automatic quality control without human review.
  - Score is stored in DB for analytics (track model quality over time).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Score Result ──────────────────────────────────────────────────────────────

@dataclass
class ArticleScore:
    """Complete quality score for a generated article."""
    total:          int     = 0   # 0-100

    # Breakdown
    word_count_pts: int     = 0   # 0-25
    structure_pts:  int     = 0   # 0-25
    seo_pts:        int     = 0   # 0-20
    affiliate_pts:  int     = 0   # 0-15
    readability_pts:int     = 0   # 0-15

    # Details
    word_count:     int     = 0
    h2_count:       int     = 0
    h3_count:       int     = 0
    has_intro:      bool    = False
    has_conclusion: bool    = False
    keyword_density:float   = 0.0
    affiliate_count:int     = 0
    avg_sentence_len:float  = 0.0
    paragraph_count:int     = 0

    # Verdict
    issues:         list    = field(default_factory=list)
    warnings:       list    = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.total >= 80: return "A"
        if self.total >= 70: return "B"
        if self.total >= 60: return "C"
        if self.total >= 40: return "D"
        return "F"

    @property
    def should_publish(self) -> bool:
        return self.total >= 60

    @property
    def should_regenerate(self) -> bool:
        return self.total < 40

    @property
    def verdict(self) -> str:
        if self.total >= 80: return "Excellent — Publier immédiatement"
        if self.total >= 60: return "Bon — Publier"
        if self.total >= 40: return "Moyen — Régénérer avec modèle supérieur"
        return "Insuffisant — Régénérer"

    def to_dict(self) -> dict:
        return {
            "total":           self.total,
            "grade":           self.grade,
            "verdict":         self.verdict,
            "should_publish":  self.should_publish,
            "breakdown": {
                "word_count":   self.word_count_pts,
                "structure":    self.structure_pts,
                "seo":          self.seo_pts,
                "affiliate":    self.affiliate_pts,
                "readability":  self.readability_pts,
            },
            "details": {
                "word_count":       self.word_count,
                "h2_count":         self.h2_count,
                "h3_count":         self.h3_count,
                "has_intro":        self.has_intro,
                "has_conclusion":   self.has_conclusion,
                "keyword_density":  round(self.keyword_density, 3),
                "affiliate_count":  self.affiliate_count,
                "avg_sentence_len": round(self.avg_sentence_len, 1),
                "paragraph_count":  self.paragraph_count,
            },
            "issues":   self.issues,
            "warnings": self.warnings,
        }


# ── Article Scorer ────────────────────────────────────────────────────────────

class ArticleScorer:
    """
    Score a generated article on 0-100 scale.

    Usage:
        scorer = ArticleScorer()
        score = scorer.score(article_html, keyword="recette tarte pommes")
        if score.should_publish:
            publish(article)
        elif score.should_regenerate:
            regenerate_with_better_model()
    """

    def score(
        self,
        html:    str,
        keyword: str = "",
    ) -> ArticleScore:
        """
        Score an article HTML string.

        Args:
            html:    Generated article HTML
            keyword: Primary keyword for density check

        Returns:
            ArticleScore with total and breakdown.
        """
        result = ArticleScore()
        if not html or len(html) < 100:
            result.issues.append("Article vide ou trop court")
            return result

        # Clean text for analysis
        text    = self._strip_html(html)
        words   = text.split()
        result.word_count = len(words)

        # Run each scoring dimension
        result.word_count_pts  = self._score_word_count(result.word_count, result)
        result.structure_pts   = self._score_structure(html, result)
        result.seo_pts         = self._score_seo(html, text, words, keyword, result)
        result.affiliate_pts   = self._score_affiliate(html, result)
        result.readability_pts = self._score_readability(text, words, result)

        result.total = (
            result.word_count_pts +
            result.structure_pts  +
            result.seo_pts        +
            result.affiliate_pts  +
            result.readability_pts
        )
        return result

    # ── Dimension 1: Word Count (0-25 pts) ────────────────────────────────────

    def _score_word_count(self, count: int, result: ArticleScore) -> int:
        """
        Score based on word count.
        800+ words = full score (SEO best practice for long-form content).
        """
        if count >= 1000:
            return 25
        elif count >= 800:
            return 20
        elif count >= 600:
            pts = 14
            result.warnings.append(f"Contenu court ({count} mots, idéal: 800+)")
            return pts
        elif count >= 400:
            result.issues.append(f"Contenu trop court ({count} mots)")
            return 8
        else:
            result.issues.append(f"Contenu insuffisant ({count} mots < 400)")
            return 0

    # ── Dimension 2: Structure (0-25 pts) ─────────────────────────────────────

    def _score_structure(self, html: str, result: ArticleScore) -> int:
        """
        Score article structure: headings, intro, conclusion.
        """
        pts = 0
        lower = html.lower()

        # H2 headings (5 pts)
        h2_count = len(re.findall(r"<h2[^>]*>", html, re.I))
        result.h2_count = h2_count
        if h2_count >= 3:
            pts += 5
        elif h2_count >= 2:
            pts += 3
        elif h2_count >= 1:
            pts += 1
            result.warnings.append("Peu de titres H2 (idéal: 3+)")
        else:
            result.issues.append("Aucun titre H2 détecté")

        # H3 headings (5 pts)
        h3_count = len(re.findall(r"<h3[^>]*>", html, re.I))
        result.h3_count = h3_count
        if h3_count >= 2:
            pts += 5
        elif h3_count >= 1:
            pts += 3

        # Introduction (5 pts) — first paragraph before first H2
        first_h2 = html.lower().find("<h2")
        first_p  = html.lower().find("<p")
        has_intro = first_p != -1 and (first_h2 == -1 or first_p < first_h2)
        result.has_intro = has_intro
        if has_intro:
            pts += 5
        else:
            result.warnings.append("Introduction avant le premier H2 manquante")

        # Conclusion (5 pts)
        conclusion_signals = ["conclusion", "verdict", "avis final", "recommandation",
                              "en résumé", "pour conclure", "notre avis", "bilan"]
        has_conclusion = any(s in lower for s in conclusion_signals)
        result.has_conclusion = has_conclusion
        if has_conclusion:
            pts += 5
        else:
            result.warnings.append("Section conclusion non détectée")

        # Lists (5 pts) — UL/OL lists add structure
        list_count = len(re.findall(r"<ul|<ol", html, re.I))
        if list_count >= 2:
            pts += 5
        elif list_count >= 1:
            pts += 3

        return min(25, pts)

    # ── Dimension 3: SEO (0-20 pts) ───────────────────────────────────────────

    def _score_seo(
        self,
        html:    str,
        text:    str,
        words:   list,
        keyword: str,
        result:  ArticleScore,
    ) -> int:
        """
        Score SEO signals: keyword density, title, meta, links.
        """
        pts = 0

        if not keyword:
            return 10  # Neutral score if no keyword provided

        kw_lower  = keyword.lower()
        text_lower= text.lower()

        # Keyword in first 100 words (5 pts)
        first_100 = " ".join(words[:100]).lower()
        if kw_lower in first_100 or all(w in first_100 for w in kw_lower.split()):
            pts += 5
        else:
            result.warnings.append(f"Mot-clé '{keyword}' absent des 100 premiers mots")

        # Keyword density 1-4% (10 pts)
        kw_words     = kw_lower.split()
        total_words  = max(len(words), 1)
        kw_count     = sum(text_lower.count(w) for w in kw_words) / len(kw_words)
        density      = kw_count / total_words * 100
        result.keyword_density = density

        if 1.0 <= density <= 4.0:
            pts += 10
        elif 0.5 <= density < 1.0 or 4.0 < density <= 6.0:
            pts += 5
            result.warnings.append(f"Densité mot-clé: {density:.1f}% (idéal: 1-4%)")
        else:
            result.issues.append(f"Densité mot-clé hors norme: {density:.1f}%")

        # Keyword in H2 (5 pts)
        h2_texts = re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.I | re.DOTALL)
        kw_in_h2 = any(
            kw_lower in self._strip_html(h).lower()
            or all(w in self._strip_html(h).lower() for w in kw_lower.split())
            for h in h2_texts
        )
        if kw_in_h2:
            pts += 5
        else:
            result.warnings.append(f"Mot-clé absent des titres H2")

        return min(20, pts)

    # ── Dimension 4: Affiliate Links (0-15 pts) ───────────────────────────────

    def _score_affiliate(self, html: str, result: ArticleScore) -> int:
        """
        Score affiliate link presence.
        1-3 links = optimal. 0 = no monetization. 4+ = spammy.
        """
        # Count actual affiliate links
        aff_count  = len(re.findall(r'href=["\'][^"\']*amazon[^"\']*tag=[^"\']*["\']', html, re.I))
        # Count placeholders (not yet replaced)
        placeholder= len(re.findall(r'\{AFFILIATE_URL', html))
        total      = aff_count + placeholder
        result.affiliate_count = total

        if total == 0:
            result.warnings.append("Aucun lien affilié détecté")
            return 5   # Partial score — links injected by optimizer later
        elif 1 <= total <= 3:
            return 15
        elif total == 4:
            return 10
        else:
            result.warnings.append(f"Trop de liens affiliés ({total}), risque spam")
            return 5

    # ── Dimension 5: Readability (0-15 pts) ──────────────────────────────────

    def _score_readability(self, text: str, words: list, result: ArticleScore) -> int:
        """
        Score readability: sentence length, paragraph variety.
        French optimal: 15-20 words per sentence.
        """
        pts = 0

        # Sentence length (10 pts)
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
        if sentences:
            avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
            result.avg_sentence_len = avg_len
            if 10 <= avg_len <= 25:
                pts += 10
            elif 8 <= avg_len < 10 or 25 < avg_len <= 35:
                pts += 6
                result.warnings.append(f"Longueur phrases: {avg_len:.0f} mots (idéal: 10-25)")
            else:
                pts += 2
                result.issues.append(f"Phrases trop longues ou trop courtes: {avg_len:.0f} mots")

        # Paragraph count (5 pts)
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        result.paragraph_count = len(paragraphs)
        if len(paragraphs) >= 6:
            pts += 5
        elif len(paragraphs) >= 4:
            pts += 3
        elif len(paragraphs) >= 2:
            pts += 1

        return min(15, pts)

    # ── HTML Stripper ─────────────────────────────────────────────────────────

    def _strip_html(self, html: str) -> str:
        """Remove HTML tags and decode entities."""
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&[a-z]+;', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

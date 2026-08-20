"""
ai/templates.py — Structured prompt templates for article generation.

Architecture decisions:
  - Each template is a dataclass with a build() method.
  - Templates produce structured prompts with clear output format.
  - Output format uses HTML headings (H2/H3) for direct WordPress/Blogger use.
  - French language by default (target market: Maroc/France).
  - Affiliate link placeholder {AFFILIATE_URL} is injected post-generation
    by SEOOptimizer to keep prompts clean.
  - Word count targets are specified per template type:
      Product review:    800-1100 words
      Comparison:       1000-1400 words (more content = better ranking)
      Buying guide:     1200-1600 words

Template types:
  - ProductReviewTemplate    : Amazon product review
  - ComparisonTemplate       : compare 2-3 products
  - BuyingGuideTemplate      : "Meilleur X pour Y" style
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProductReviewTemplate:
    """
    Template for an Amazon product review article.

    Generates a balanced, SEO-optimized product review with
    pros/cons, use cases, and purchase recommendation.
    """
    title:          str
    asin:           str
    price:          Optional[str]    = None
    rating:         Optional[float]  = None
    reviews_count:  Optional[int]    = None
    brand:          str              = ""
    category:       str              = ""
    description:    str              = ""
    features:       list[str]        = field(default_factory=list)
    keywords:       list[str]        = field(default_factory=list)
    language:       str              = "fr"
    marketplace:    str              = "amazon.fr"

    def build(self) -> str:
        lang_inst   = "en français" if self.language == "fr" else "in English"
        kw_str      = ", ".join(self.keywords[:5]) if self.keywords else self.title
        feat_list   = "\n".join(f"- {f}" for f in self.features[:8])

        price_info  = f"Prix : {self.price}" if self.price else ""
        rating_info = f"Note : {self.rating}/5 ({self.reviews_count} avis)" if self.rating else ""

        return f"""Tu es un expert en test et comparatif de produits. Rédige un article de test/avis complet {lang_inst} sur ce produit Amazon.

PRODUIT : {self.title}
Marque : {self.brand or "Non spécifiée"}
Catégorie : {self.category or "Non spécifiée"}
{price_info}
{rating_info}
{"Description : " + self.description if self.description else ""}

CARACTÉRISTIQUES :
{feat_list if feat_list else "- Produit de qualité"}

CONSIGNES DE RÉDACTION :
- Longueur : 800 à 1100 mots
- Mots-clés SEO : {kw_str}
- Structure HTML avec <h2> et <h3>
- Introduction : présentation du produit et pourquoi ce test
- Section "Caractéristiques techniques" : détails précis
- Section "Points forts" : liste <ul> des avantages
- Section "Points faibles" : liste <ul> des inconvénients (soyez honnête)
- Section "Pour qui est-il fait ?" : profils d'utilisateurs idéaux
- Section "Notre verdict" : note finale et recommandation
- Conclusion avec appel à l'action naturel
- Ton objectif et professionnel mais accessible
- NE PAS inventer de tests ou spécifications non mentionnées
- Placeholder {{AFFILIATE_URL}} pour le lien d'achat

FORMAT DE SORTIE :
Retourne uniquement l'article HTML, sans commentaires."""


@dataclass
class ComparisonTemplate:
    """
    Template for a product comparison article (2-3 products).
    """
    products:   list[dict]      # [{"title": ..., "asin": ..., "price": ..., "rating": ...}]
    category:   str
    keywords:   list[str]       = field(default_factory=list)
    language:   str             = "fr"

    def build(self) -> str:
        lang_inst = "en français" if self.language == "fr" else "in English"
        kw_str    = ", ".join(self.keywords[:5]) if self.keywords else self.category

        prod_list = ""
        for i, p in enumerate(self.products[:3], 1):
            prod_list += f"\nPRODUIT {i} : {p.get('title', 'Produit')}\n"
            if p.get('price'):   prod_list += f"  Prix : {p['price']}\n"
            if p.get('rating'):  prod_list += f"  Note : {p['rating']}/5\n"
            if p.get('brand'):   prod_list += f"  Marque : {p['brand']}\n"

        return f"""Tu es un expert comparatif produit. Rédige un article de comparaison complet {lang_inst}.

CATÉGORIE : {self.category}
MOTS-CLÉS : {kw_str}

PRODUITS À COMPARER :
{prod_list}

CONSIGNES :
- Longueur : 1000 à 1400 mots
- Structure HTML avec <h2> et <h3>
- Introduction : contexte et critères de comparaison
- Présentation individuelle de chaque produit (section <h2> par produit)
- Tableau comparatif HTML (<table>) avec : Prix, Note, Points forts, Points faibles
- Section "Lequel choisir ?" selon les profils (débutant, expert, budget serré...)
- Conclusion avec recommandation claire
- Placeholders {{AFFILIATE_URL_1}}, {{AFFILIATE_URL_2}}, {{AFFILIATE_URL_3}}
- Ton neutre et informatif

FORMAT DE SORTIE :
Retourne uniquement l'article HTML, sans commentaires."""


@dataclass
class BuyingGuideTemplate:
    """
    Template for a buying guide ("Meilleur X pour Y").
    """
    category:     str
    products:     list[dict]     = field(default_factory=list)
    criteria:     list[str]      = field(default_factory=list)
    keywords:     list[str]      = field(default_factory=list)
    target_buyer: str            = "grand public"
    language:     str            = "fr"

    def build(self) -> str:
        lang_inst  = "en français" if self.language == "fr" else "in English"
        kw_str     = ", ".join(self.keywords[:5]) if self.keywords else self.category
        crit_list  = "\n".join(f"- {c}" for c in self.criteria[:6])

        prod_section = ""
        for i, p in enumerate(self.products[:5], 1):
            prod_section += f"\n{i}. {p.get('title', 'Produit')} — {p.get('price', '')} — Note {p.get('rating', '')}/5"

        return f"""Tu es un expert en guide d'achat. Rédige un guide d'achat complet {lang_inst}.

CATÉGORIE : {self.category}
CIBLE : {self.target_buyer}
MOTS-CLÉS : {kw_str}

CRITÈRES DE SÉLECTION :
{crit_list if crit_list else "- Qualité, prix, disponibilité"}

PRODUITS SÉLECTIONNÉS :
{prod_section if prod_section else "- Meilleurs produits de la catégorie"}

CONSIGNES :
- Longueur : 1200 à 1600 mots
- Structure HTML avec <h2> et <h3>
- Introduction : pourquoi ce guide et comment choisir
- Section "Critères de sélection" : expliquer chaque critère
- Section "Notre sélection" : présenter chaque produit (<h3> par produit)
- Section "Conseils d'achat" : pièges à éviter, conseils pratiques
- Section "FAQ" : 3-4 questions fréquentes avec réponses
- Conclusion avec appel à l'action
- Placeholders {{AFFILIATE_URL_N}} pour chaque produit
- Ton expert mais accessible

FORMAT DE SORTIE :
Retourne uniquement l'article HTML, sans commentaires."""

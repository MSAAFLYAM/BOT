# NestDeal — Developer Brief v1.0
**Site actuel:** https://nestdeal.blogspot.com  
**Plateforme actuelle:** Blogger (Blogspot) — XML theme v4.2  
**Objectif:** Migrer vers un site indépendant de haute qualité  
**Type:** Amazon Affiliate — Reviews & comparaisons Smart Home / Home Security  
**Affiliate tag Amazon:** `nestdeal-20` (à injecter sur tous les liens Amazon)

---

## 1. VISION DU SITE

NestDeal n'est **pas** un site de listing de produits Amazon.  
C'est un **site de reviews éditoriaux** qui :

- Examine les produits Smart Home et Home Security en profondeur
- Compare les produits côte à côte avec un verdict clair
- Recommande la **meilleure alternative** quand un produit est faible
- Dit clairement au visiteur quand **un produit ne vaut pas l'achat**
- Monétise via le programme **Amazon Associates** (liens affiliés)
- Cible des visiteurs qui veulent **le meilleur choix** avant d'acheter

### Positionnement éditorial
| Ce que le site fait | Ce que le site ne fait PAS |
|---|---|
| Review honnête et détaillée | Lister tous les produits Amazon |
| Verdict clair : buy / consider / skip | Afficher des prix en temps réel |
| Proposer un meilleur produit alternatif | Prétendre tester physiquement les produits |
| Comparer 2–4 produits en table | Écrire du contenu générique |
| Contenu généré par IA + édité | Copier des reviews Amazon |

---

## 2. STACK RECOMMANDÉ

### Option A — Next.js (recommandé)
```
Framework : Next.js 14+ (App Router)
Styling    : Tailwind CSS
CMS        : Contentlayer ou MDX (articles en Markdown)
Génération : Script Python → MDX files → Next.js static pages
Hébergement: Vercel (gratuit tier généreux)
Images     : next/image + Cloudinary (plan gratuit)
Sitemap    : next-sitemap
Analytics  : Google Analytics 4 (déjà configuré: G-VKXSW648BH)
```

### Option B — Astro (plus simple, plus rapide)
```
Framework : Astro 4+
Styling    : Tailwind CSS
CMS        : Contenu en Markdown/MDX
Génération : Script Python → .md files
Hébergement: Vercel ou Netlify
```

### Option C — Rester sur Blogger (minimal)
```
Garder le thème v4.2 XML
Améliorer uniquement le générateur d'articles Python
Aucune migration nécessaire
```

**Ce brief couvre l'Option A (Next.js) — la plus scalable.**

---

## 3. ARCHITECTURE DU PROJET

```
nestdeal/
├── app/
│   ├── page.tsx                    # Homepage
│   ├── [category]/
│   │   └── page.tsx                # Pages catégorie (Smart Home, etc.)
│   ├── reviews/
│   │   └── [slug]/
│   │       └── page.tsx            # Page article review
│   ├── compare/
│   │   └── [slug]/
│   │       └── page.tsx            # Page comparaison
│   └── layout.tsx                  # Layout global (header/footer)
│
├── components/
│   ├── layout/
│   │   ├── Header.tsx
│   │   ├── Footer.tsx
│   │   ├── NavStrip.tsx
│   │   └── MobileDrawer.tsx
│   ├── home/
│   │   ├── Hero.tsx                # Caché sur mobile
│   │   ├── CategoryStrip.tsx
│   │   └── ProductGrid.tsx
│   ├── review/
│   │   ├── ReviewHeader.tsx        # Titre + meta + badge
│   │   ├── VerdictBox.tsx          # Score + verdict + recommend
│   │   ├── ProsCons.tsx            # Grille pros/cons
│   │   ├── ScoreBars.tsx           # Barres de scores
│   │   ├── SpecTable.tsx           # Table de specs
│   │   ├── ComparisonTable.tsx     # Comparaison produits
│   │   ├── AmazonButton.tsx        # Bouton achat affilié
│   │   ├── AlternativeBox.tsx      # Produit alternatif
│   │   ├── NotRecommended.tsx      # Box "skip this product"
│   │   ├── FaqAccordion.tsx        # FAQ avec accordion
│   │   ├── ReviewSidebar.tsx       # Sidebar sticky desktop
│   │   └── StickyCta.tsx           # Bottom bar CTA mobile/desktop
│   └── ui/
│       ├── Badge.tsx
│       ├── ScoreCircle.tsx
│       └── RelatedPosts.tsx
│
├── content/
│   └── reviews/
│       └── ghome-smart-plug.mdx    # Articles générés par Python
│
├── lib/
│   ├── affiliate.ts                # Injection tag Amazon
│   ├── seo.ts                      # generateMetadata helper
│   └── schema.ts                   # JSON-LD Product schema
│
├── scripts/
│   └── generate-article.py        # Générateur IA d'articles
│
└── public/
    └── images/
```

---

## 4. DESIGN SYSTEM — Palette et tokens

```css
/* Reprendre exactement ces valeurs du thème Blogger v4.2 */
:root {
  --navy:       #0B1E3D;   /* Header, nav, footer, badges dark */
  --navy-mid:   #142848;   /* Nav strip background */
  --navy-light: #1A3359;   /* Hero gradient end */
  --orange:     #F59E0B;   /* CTA, highlights, accent */
  --orange-dk:  #D97706;   /* Links, hover states */
  --orange-lt:  #FEF3C7;   /* Badge backgrounds, blockquote bg */
  --bg:         #EFF2F7;   /* Background — warm slate (identité tech review) */
  --surface:    #FFFFFF;   /* Cards, panels */
  --ink:        #111827;   /* Headings */
  --ink-mid:    #374151;   /* Body text */
  --ink-soft:   #6B7280;   /* Meta, secondary text */
  --ink-muted:  #9CA3AF;   /* Placeholders */
  --border:     #E5E7EB;   /* Card borders */
  --border-mid: #D1D5DB;   /* Stronger borders */
  --green:      #059669;   /* "Buy" verdict */
  --green-lt:   #ECFDF5;   /* Pros box background */
}
```

### Règles design
- **Header** : navy `#0B1E3D` — toujours dark, toujours sticky
- **Background** : `#EFF2F7` sur TOUT le site sauf header/footer
- **Cards** : blanc `#FFFFFF` avec border `#E5E7EB`
- **Hero** : visible desktop/tablette uniquement — `display:none` sous 768px
- **CTA primaire** : orange `#F59E0B` avec texte navy
- **Radius** : 10px cards, 6px boutons, 4px badges

---

## 5. SYSTÈME DE REVIEW — Schéma de données

Chaque article review est un fichier MDX avec ce frontmatter :

```yaml
---
title: "GHome Smart Plug WiFi Review — Worth It in 2026?"
slug: "ghome-smart-plug-wifi-review"
category: "Smart Home"
date: "2026-09-05"
updated: "2026-09-05"

# Scores (sur 10)
score: 7.8
score_value: 8
score_build: 7
score_features: 9
score_privacy: 5

# Verdict
recommend: "buy"           # buy | consider | skip
verdict: "Great value for
 Alexa/Google users, but requires cloud — no local control."

# Amazon
amazon_url: "https://www.amazon.com/dp/B0ASIN?tag=nestdeal-20"
amazon_asin: "B0XXXXXXXX"

# Alternative (toujours présent, surtout si recommend=skip)
alt_name: "Kasa Smart Plug EP25"
alt_url: "https://www.amazon.com/dp/B0ALT?tag=nestdeal-20"
alt_why: "Local control, no subscription, same price"
alt_asin: "B0ALTASIN"

# SEO
meta_description: "GHome Smart Plug review: honest pros, cons and verdict. Is it worth buying in 2026?"
og_image: "/images/reviews/ghome-smart-plug.jpg"

# Comparisons
compare_with:
  - name: "Kasa EP25"
    asin: "B0ALT"
    price_approx: "$18"
  - name: "Meross MSS210"
    asin: "B0ALT2"
    price_approx: "$12"
---
```

---

## 6. COMPOSANTS REVIEW DÉTAILLÉS

### 6.1 VerdictBox
```tsx
// Affiche score, verdict one-liner, badge recommend
// recommend="buy"     → vert  "We Recommend"
// recommend="consider"→ amber "Worth Considering"
// recommend="skip"    → rouge "We Say Skip It"

interface VerdictBoxProps {
  score: number           // 0–10
  verdict: string         // one sentence
  recommend: 'buy' | 'consider' | 'skip'
  scoreValue: number
  scoreBuild: number
  scoreFeatures: number
  scorePrivacy: number
}
```

### 6.2 AmazonButton
```tsx
// Toujours rel="nofollow sponsored noopener noreferrer"
// Toujours target="_blank"
// Toujours tag affiliate dans l'URL
// GA4 event au clic

interface AmazonButtonProps {
  url: string             // URL complète avec ?tag=nestdeal-20
  label?: string          // défaut: "Check Price on Amazon"
  size?: 'sm' | 'md' | 'lg'
  variant?: 'primary' | 'outline'
}

// onClick handler:
gtag('event', 'amazon_click', {
  event_category: 'affiliate',
  link_url: url,
  page_location: window.location.href,
  recommend: recommend,   // buy/consider/skip
})
```

### 6.3 AlternativeBox
```tsx
// Toujours affiché si alt_name est défini
// Mis en avant si recommend="skip"
// Box orange border avec badge "Better Alternative"
// Si l'alternative n'est PAS sur le site → lien Amazon direct avec tag

interface AlternativeBoxProps {
  altName: string
  altUrl: string          // Amazon URL avec tag
  altWhy: string          // Raison courte
  altIsOnSite?: boolean   // true = lien interne, false = lien Amazon direct
  altSiteUrl?: string     // URL interne si altIsOnSite=true
}
```

### 6.4 StickyCta
```tsx
// Bottom bar fixe sur toutes les pages review
// Desktop: max-width 600px centré
// Mobile: full-width
// Se cache en scrollant vers le bas, réapparaît en scrollant vers le haut
// Affiche: nom du produit (tronqué 50 chars) + bouton Amazon

interface StickyCtaProps {
  productName: string
  amazonUrl: string
  recommend: 'buy' | 'consider' | 'skip'
}
// Si recommend="skip" → le bouton CTA pointe vers l'alternative, pas le produit
```

### 6.5 ComparisonTable
```tsx
// Table responsive avec overflow-x:auto
// Colonne produit principal → badge "Reviewed"
// winner class → texte vert
// loser class  → texte rouge
// Données viennent du frontmatter compare_with[]
```

---

## 7. SEO — Implémentation Next.js

### generateMetadata (par page review)
```tsx
export async function generateMetadata({ params }): Promise<Metadata> {
  const review = getReview(params.slug)
  return {
    title: review.title,
    description: review.meta_description,
    openGraph: {
      title: review.title,
      description: review.meta_description,
      images: [review.og_image],
      type: 'article',
    },
    alternates: {
      canonical: `https://nestdeal.com/reviews/${review.slug}`,
    },
    robots: { index: true, follow: true },
  }
}
```

### JSON-LD Product Schema (par page review)
```tsx
// Dans le <head> de chaque page review
const productSchema = {
  '@context': 'https://schema.org',
  '@type': 'Product',
  name: review.title,
  description: review.verdict,
  image: review.og_image,
  url: `https://nestdeal.com/reviews/${review.slug}`,
  brand: { '@type': 'Organization', name: 'NestDeal' },
  offers: {
    '@type': 'Offer',
    url: review.amazon_url,
    priceCurrency: 'USD',
    availability: 'https://schema.org/InStock',
    seller: { '@type': 'Organization', name: 'Amazon' },
  },
}

// JSON-LD Review Schema (optionnel mais puissant)
const reviewSchema = {
  '@context': 'https://schema.org',
  '@type': 'Review',
  itemReviewed: { '@type': 'Product', name: review.title },
  reviewRating: {
    '@type': 'Rating',
    ratingValue: review.score,
    bestRating: 10,
    worstRating: 0,
  },
  author: { '@type': 'Organization', name: 'NestDeal' },
  reviewBody: review.verdict,
}
```

### Robots — même logique que Blogger v4.2
```tsx
// Pages catégorie → index, follow
// Pages de recherche → noindex, nofollow
// Toutes les pages review → index, follow
// Pages statiques (About, Privacy) → index, follow
```

### Sitemap (next-sitemap)
```js
// next-sitemap.config.js
module.exports = {
  siteUrl: 'https://nestdeal.com',
  generateRobotsTxt: true,
  changefreq: 'weekly',
  priority: 0.7,
  exclude: ['/search*'],
}
```

---

## 8. GÉNÉRATEUR D'ARTICLES PYTHON — Prompt système corrigé

```python
SYSTEM_PROMPT = """
You are a senior product reviewer for NestDeal, a Smart Home and Home Security 
Amazon affiliate blog. Your reviews are honest, detailed, and editorial.

WRITING RULES:
- Write in English, professional but accessible tone
- Be honest — if a product is weak, say so clearly
- Always recommend an alternative when verdict is "skip" or "consider"
- Never claim to have physically tested a product unless the article says so
- Never write "Best prices" or "Lowest prices" — Amazon pricing changes daily

VERDICT SYSTEM:
- "buy"     : Product is clearly worth purchasing
- "consider": Product has notable trade-offs, worth it for specific users
- "skip"    : Product is not recommended — MUST include an alternative

AFFILIATE RULES:
- Every Amazon link MUST include ?tag=nestdeal-20
- rel="nofollow sponsored noopener noreferrer" on every Amazon link
- target="_blank" on every Amazon link

CHARACTER ENCODING — CRITICAL:
- NEVER use HTML entities in visible text: no &#8722; &#8250; &amp; &#8211; etc.
- Use real Unicode: − (U+2212) not &#8722;, › not &#8250;, & not &amp;
- Exception: HTML attributes may use standard &quot; &amp; where required by HTML spec

FORBIDDEN CONTENT:
- Do not add an Amazon disclosure box in the article body (theme handles it)
- Do not add "Expert Review" badge (theme generates it)
- Do not add "Updated [date]" text (handled by frontmatter)
- Do not add "X,XXX+ verified ratings" — never hardcode review counts

REQUIRED OUTPUT STRUCTURE:
1. data-nd-review span (hidden, with all data attributes)
2. Verdict summary paragraph
3. Pros & Cons grid
4. Amazon buy button (or Not Recommended box if skip)
5. Alternative product box (always if alt defined)
6. Key Specifications table
7. Full review body with H2/H3 structure
8. Comparison table (if compare_with has entries)
9. FAQ accordion (3–5 questions, use real − character for collapse icon)
10. Final Verdict section
"""

# Post-processing — ALWAYS apply after AI generation
import re, html

def clean_article(content: str, tag: str = "nestdeal-20") -> str:
    # 1. Unescape HTML entities
    content = html.unescape(content)
    
    # 2. Add affiliate tag to Amazon links missing it
    def add_tag(m):
        url = m.group(0)
        if 'tag=' in url: return url
        sep = '&' if '?' in url else '?'
        return url + sep + 'tag=' + tag
    content = re.sub(
        r'https?://(?:www\.)?amazon\.[a-z.]+/[^\s"\'<>]+',
        add_tag, content
    )
    
    # 3. Ensure rel and target on Amazon links
    content = re.sub(
        r'(<a\s[^>]*href=["\']https?://(?:www\.)?amazon\.[^"\']+["\'])',
        lambda m: m.group(0) + ' rel="nofollow sponsored noopener noreferrer" target="_blank"'
        if 'rel=' not in m.group(0) else m.group(0),
        content
    )
    
    return content
```

---

## 9. MIGRATION DEPUIS BLOGGER

### Étape 1 — Exporter le contenu existant
```python
# Blogger API v3 — exporter tous les posts
import requests

API_KEY = "YOUR_BLOGGER_API_KEY"
BLOG_ID = "YOUR_BLOG_ID"  # visible dans l'URL Blogger

def export_posts():
    posts = []
    token = None
    while True:
        url = f"https://www.blogger.com/feeds/{BLOG_ID}/posts/default"
        params = {"alt": "json", "max-results": 50, "key": API_KEY}
        if token: params["pageToken"] = token
        r = requests.get(url, params=params).json()
        posts.extend(r.get("items", []))
        token = r.get("nextPageToken")
        if not token: break
    return posts
```

### Étape 2 — Convertir HTML → MDX
```python
from markdownify import markdownify as md

def html_to_mdx(post):
    content = md(post["content"], heading_style="ATX")
    frontmatter = f"""---
title: "{post['title']}"
slug: "{post['url'].split('/')[-1].replace('.html', '')}"
date: "{post['published'][:10]}"
category: "{post.get('labels', ['Uncategorized'])[0]}"
# TODO: add score, verdict, recommend manually or via AI
---
"""
    return frontmatter + content
```

### Étape 3 — Redirects 301 (Blogger → nouveau domaine)
```
# Dans next.config.js
async redirects() {
  return [
    {
      source: '/2026/:month/:slug.html',
      destination: '/reviews/:slug',
      permanent: true,
    },
  ]
}
```

---

## 10. PERFORMANCE TARGETS

| Métrique | Cible | Outil |
|---|---|---|
| LCP (Largest Contentful Paint) | < 2.5s | PageSpeed Insights |
| CLS (Cumulative Layout Shift) | < 0.1 | Chrome DevTools |
| FID / INP | < 200ms | Web Vitals |
| Images | WebP, lazy loaded, sized | next/image |
| Fonts | Preload Inter, system fallback | next/font |
| JS bundle | < 150KB gzipped | next build analyzer |

---

## 11. ANALYTICS — GA4 Events à implémenter

```typescript
// Tous les events déjà dans le thème Blogger v4.2 — à porter en Next.js

// Clic sur bouton Amazon (principal KPI)
gtag('event', 'amazon_click', {
  event_category: 'affiliate',
  link_url: url,
  link_text: label,
  page_location: window.location.href,
  recommend: 'buy' | 'consider' | 'skip',
})

// Clic Sticky CTA
gtag('event', 'amazon_sticky_cta', {
  event_category: 'affiliate',
  link_url: url,
  page_location: window.location.href,
})

// Navigation catégorie
gtag('event', 'category_click', {
  event_category: 'navigation',
  event_label: categoryName,
})

// Alternative product click
gtag('event', 'alternative_click', {
  event_category: 'affiliate',
  link_url: altUrl,
  alt_name: altName,
  page_location: window.location.href,
})

// Scroll depth (optionnel mais utile)
gtag('event', 'scroll', {
  event_category: 'engagement',
  percent_scrolled: 25 | 50 | 75 | 100,
})
```

---

## 12. COMPLIANCE — Amazon Associates

| Règle | Implémentation |
|---|---|
| Disclosure "As an Amazon Associate I earn from qualifying purchases." | Footer sur toutes les pages |
| Pas de prix hardcodés | Jamais de prix dans le code — toujours "Check Price on Amazon" |
| rel="sponsored" sur tous les liens Amazon | AmazonButton component applique automatiquement |
| Pas de claims "Best Price" | Interdit dans le prompt IA et dans les composants |
| Pas d'images Amazon produit sans autorisation | Utiliser images propres ou PA-API avec attribution |
| Affiliate tag sur tous les liens | Fonction `clean_article()` en post-processing |

---

## 13. CHECKLIST DE LANCEMENT

- [ ] Domaine configuré (ex: nestdeal.com)
- [ ] Vercel déployé + domaine custom
- [ ] GA4 `G-VKXSW648BH` configuré dans Next.js
- [ ] Google Search Console — nouveau domaine soumis
- [ ] Sitemap soumis dans Search Console
- [ ] Redirects 301 depuis nestdeal.blogspot.com
- [ ] Tous les articles migrés avec frontmatter complet
- [ ] Test affiliate tag sur 10 liens aléatoires
- [ ] Test JSON-LD sur Google Rich Results Test
- [ ] Test mobile — hero caché, CTA visible, sidebar cachée
- [ ] Test desktop — layout 2 colonnes review, full width
- [ ] PageSpeed score > 90 mobile et desktop
- [ ] Disclosure Amazon visible sur toutes les pages

---

## 14. COULEURS IDENTITÉ — Référence visuelle

```
Background site :  #EFF2F7  ← warm slate, identité tech review
Header/Footer   :  #0B1E3D  ← navy profond, autorité
Accent/CTA      :  #F59E0B  ← amber chaud, action
Texte principal :  #111827  ← quasi-noir, lisibilité
Cards           :  #FFFFFF  ← blanc pur, contraste sur bg
```

**Logique de couleur :** Le slate-blue `#EFF2F7` est différent d'un gris neutre — il donne une teinte froide/tech subtile qui rappelle l'univers de la domotique et de la sécurité connectée sans être agressif.

---

*Brief généré par Claude pour NestDeal — Septembre 2026*  
*Thème Blogger de référence : nestdeal-theme-v4.2.xml*

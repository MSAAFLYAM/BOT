# Fix Published Articles — Nest Deal Bot

## Project Context

This is a **Telegram bot** that scrapes Amazon products and auto-publishes articles to **Blogger** (nestdeal.blogspot.com).

### Architecture
```
Amazon URL → Scraper (get product data) → AI (generate content) → Blogger API (publish)
```

### Key Files
| File | Purpose |
|------|---------|
| `blogger_api_publisher.py` | Builds HTML article + publishes to Blogger |
| `scraper.py` | Scrapes Amazon product data (title, price, image, features) |
| `content_generator.py` | Generates product descriptions via AI |
| `config.py` | Environment variables (BOT_TOKEN, AFFILIATE_TAG, etc.) |
| `.env` | Secret keys and configuration |

### Current Bugs in Published Articles

#### Bug 1: Price in MAD (Moroccan Dirham) instead of USD
**Root Cause:** The scraper reads Amazon prices based on IP geolocation. Since the server is in Morocco, Amazon shows MAD prices.

**Fix Applied:** `_normalize_price_to_usd()` function in scraper.py now strips all currency symbols and forces `$` prefix.

**Example:**
- Before: `MAD279.51`
- After: `$279.51`

#### Bug 2: Placeholder Pros/Cons text
**Root Cause:** When AI providers (Groq/OpenRouter) fail or are not configured, the code falls back to hardcoded generic content. Also, the Blogger theme may have its own placeholder sections.

**Fix Applied:** 
- Added GROQ_API_KEY for AI content generation
- `_ai_generate_content()` generates product-specific Pros/Cons
- Pre-publish validation blocks articles with placeholder patterns

**Example:**
- Before: "Add your first pro point here"
- After: "Hexagon-shaped ends prevent rolling during storage"

#### Bug 3: Placeholder Specifications
**Root Cause:** Product Specifications table (Brand, Model, Weight, etc.) is part of the Blogger THEME template, not the article content. The bot doesn't fill these fields.

**Fix Required:** Either:
1. Remove the Specifications section from the article HTML (since the theme adds its own)
2. Or fill the Specifications with scraped data (ASIN, Brand, Weight from Amazon)

### How Articles Are Built

The `_build_article()` function in `blogger_api_publisher.py` generates:

```html
<!-- Product Image -->
<img src="..." alt="Product Title">

<!-- AI-Generated Intro -->
<p>The [product] is a top-rated Amazon product...</p>

<!-- AI-Generated Why You'll Like It -->
<h2>Why You'll Like It</h2>
<p>[AI content specific to this product]</p>

<!-- AI-Generated Best For -->
<h2>Best For</h2>
<ul>
  <li>Specific use case 1</li>
  <li>Specific use case 2</li>
  ...
</ul>

<!-- Product Card -->
<div class="deal-card">
  <h2>Product Title</h2>
  <div class="price">$XX.XX</div>  <!-- MUST be USD -->
  <div class="rating">4.7/5 (1,234 reviews)</div>
  <a href="https://amazon.com/dp/ASIN?tag=dazzledeals00-20">Buy Now</a>
</div>

<!-- AI-Generated Final Thoughts -->
<h2>Final Thoughts</h2>
<p>[AI closing paragraph]</p>
```

### How to Fix Published Articles

#### Option 1: Manual Fix on Blogger
1. Go to Blogger Dashboard → Posts
2. Edit each article
3. Replace MAD prices with USD prices
4. Replace placeholder Pros/Cons with real content
5. Remove or fill Specifications table

#### Option 2: Re-publish with Bot
1. Delete the old article from Blogger
2. Re-run the pipeline with the same Amazon URL
3. The bot will generate a new article with correct prices and AI content

#### Option 3: Bulk Fix Script
Use the `fix_published_articles.py` script to:
1. Fetch all published articles from Blogger
2. Extract product data (ASIN, title)
3. Re-scrape Amazon for USD prices
4. Re-generate content with AI
5. Update the articles

### Environment Variables

```env
# Telegram
BOT_TOKEN=7904229690:AAH4W_...
CHANNEL_ID=-1002366175623
ADMIN_CHAT_ID=963761857

# AI (Groq - Free)
GROQ_API_KEY=your_groq_api_key_here

# Blogger
BLOGGER_CLIENT_ID=84260044695-...
BLOGGER_CLIENT_SECRET=GOCSPX-...
BLOGGER_REFRESH_TOKEN=1//03AqAVy-...
BLOGGER_BLOG_ID=4921831860521399892

# Amazon Affiliate
AFFILIATE_TAG=dazzledeals00-20
```

### Validation Rules (Pre-publish)

Before publishing, the bot checks:
1. ✅ Price starts with `$` (not MAD, EUR, etc.)
2. ✅ No placeholder text ("Add your", "Edit brand", etc.)
3. ✅ Affiliate link contains `dazzledeals00-20`
4. ✅ No `href="#"` broken links
5. ✅ Title not truncated mid-word

### For AI Assistants

When working on this project:
1. Always use `dazzledeals00-20` as the affiliate tag
2. Always force USD prices (strip MAD/EUR/GBP symbols)
3. Generate product-specific content (not generic)
4. Validate before publishing
5. Never publish articles with placeholder text

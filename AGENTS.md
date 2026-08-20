# AGENTS.md — Amazon Affiliate Bot (Full Project Context for AI Assistants)

> **Last updated:** 2026-07-18
> **Runtime:** Python 3.13 | python-telegram-bot | Flask
> **Bot token:** Set in `.env`
> **Bot username:** Check Telegram
> **Channel:** -1002366175623
> **Database:** LOCAL SQLite (auto-detected)
> **Cache:** Local file-based (auto-detected)
> **Deployment:** Local Windows machine (`python main.py`)
> **Working directory:** `C:\Users\user\Desktop\bot-working`

---

## 1. PROJECT OVERVIEW

Amazon Affiliate Bot is a fully automated product review platform that:
1. **Discovers** Amazon products via URL input or /discover command
2. **Scrapes** product data using 5-layer fallback (Desktop → Mobile → AW → Jina AI → Apify)
3. **Extracts** title, price, rating, reviews, features, images
4. **Generates** AI content via Groq (Pros, Cons, Specs, FAQ, Verdict)
5. **Builds** premium HTML article with inline CSS (Playfair Display + Lora fonts)
6. **Publishes** to Blogger with affiliate tag `dazzledeals00-20`
7. **Alerts** Telegram channel with product link

---

## 2. CRITICAL CONTEXT (Read Before Editing)

### Bot Token & 409 Conflict
- Current bot token: Set in `.env`
- If you see "409 Conflict" errors, another bot instance is running
- Kill other Python processes: `taskkill /F /IM python.exe`

### No Cloud Services — Everything Local
- **Database:** SQLite (auto-detected when `DATABASE_URL` is empty)
- **Cache:** File-based JSON cache (auto-detected when `REDIS_URL` is empty)
- **No Railway, No Docker, No GitHub Actions**

### Affiliate Tag
- **Default tag:** `dazzledeals00-20`
- All Amazon links MUST include `tag=dazzledeals00-20`
- Configured in `config.py` and `scraper.py`

### Price Normalization
- All prices MUST display in USD ($), never MAD/EUR/GBP
- `scraper.py` has `_normalize_price_to_usd()` that converts MAD prices

### Blogger API
- Client ID: Set in `.env`
- Client Secret: Set in `.env`
- Refresh Token: Set in `.env`
- Blog ID: Set in `.env`
- Blog URL: `https://nestdeal.blogspot.com/`

### AI Content Generation
- Uses GROQ_API_KEY for free AI content (Groq free tier)
- Model: `llama-3.3-70b-versatile`
- Generates: intro, why_like, best_for, pros, cons, specs, verdict, faq, final

---

## 3. PROJECT STRUCTURE

```
bot-working/
├── main.py                     # Entry point: Flask app + bot + scheduler
├── .env                        # Environment variables (secrets)
├── .env.example                # Environment variables template
├── requirements.txt            # Python dependencies
├── scraper.py                  # Amazon scraper (5-layer fallback)
├── content_generator.py        # AI description generator
├── blogger_api_publisher.py    # Blogger API publisher + article builder
├── config.py                   # Environment variable loading
├── fix_published_articles.py   # Bulk article fix/update script
├── preview_template.html       # Template preview file
│
├── ai/                         # AI providers
│   └── providers/
│       └── template.py         # Fallback HTML template
│
├── handlers/                   # Telegram command handlers
│   ├── cmd_help.py             # /start, /help
│   ├── cmd_discover.py         # /discover [url] [n]
│   ├── cmd_addurl.py           # /addurl <url>
│   ├── cmd_testai.py           # /testai
│   ├── cmd_health.py           # /health
│   ├── cmd_stats.py            # /stats
│   └── cmd_sources.py          # /listsources, /addsource, /rmsource
│
├── core/                       # Core services
│   ├── db.py                   # SQLite database
│   ├── cache.py                # File-based cache
│   └── config.py               # Configuration
│
├── dashboard/                  # Flask web dashboard
│   ├── routes.py               # Dashboard routes
│   └── templates/              # HTML templates
│
└── data/                       # Data files
    └── cache/                  # File cache storage
```

---

## 4. BOT COMMANDS

### Discovery & Publishing
| Command | Description |
|---------|-------------|
| `/discover [url] [n]` | Discover & publish n products from Amazon |
| `/addurl <url>` | Process a single Amazon URL |
| `/batch` | Batch publish from .txt file (one keyword per line) |
| *Send any URL* | Bot auto-detects and processes direct URLs |

### AI & Testing
| Command | Description |
|---------|-------------|
| `/testai` | Test AI providers |
| `/testimage [url]` | Test image transformation |
| `/telegramcheck` | Test Telegram channel connection |
| `/sendtest` | Send test message to channel |

### Status & Monitoring
| Command | Description |
|---------|-------------|
| `/health` | Check all services |
| `/stats` | Dashboard stats |
| `/listsources` | List monitored sources |
| `/addsource <url>` | Add RSS feed source |

### General
| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Show all commands |

---

## 5. ENVIRONMENT VARIABLES (`.env`)

```
# ── Telegram ──
BOT_TOKEN=your_bot_token_here
ADMIN_CHAT_ID=963761857
CHANNEL_ID=-1002366175623

# ── Database (empty = SQLite) ──
DATABASE_URL=

# ── Cache (empty = file cache) ──
REDIS_URL=

# ── Blogger API ──
BLOGGER_CLIENT_ID=your_client_id_here
BLOGGER_CLIENT_SECRET=your_client_secret_here
BLOGGER_REFRESH_TOKEN=your_refresh_token_here
BLOG_ID=your_blog_id_here

# ── AI (Groq free tier) ──
GROQ_API_KEY=your_groq_api_key_here

# ── Affiliate ──
AFFILIATE_TAG=dazzledeals00-20

# ── Image Transformation (avoid copyright) ──
IMAGE_TRANSFORM_PRESET=auto
IMAGE_TRANSFORM_ENABLED=1

# ── Other ──
PORT=8080
PUBLIC_URL=                     # Empty = polling mode
ENVIRONMENT=development
```

---

## 6. HOW TO RUN

```powershell
# 1. Activate virtual environment
cd C:\Users\user\Desktop\bot-working
.\venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the bot
python main.py
```

The bot starts:
- Flask dashboard on http://127.0.0.1:8080
- Telegram bot polling mode
- Daily scheduler

---

## 7. PIPELINE FLOW

### Single Product (URL or /addurl)
```
User sends Amazon URL or /discover command
    ↓
5-Layer Fetch: Desktop UA → Mobile UA → AW Page → Jina AI Reader → Apify
    ↓
Extract: title, price, rating, reviews, features, image, ASIN
    ↓
Price Normalization: MAD/EUR/GBP → USD ($)
    ↓
Build Affiliate URL: amazon.com/dp/ASIN?tag=dazzledeals00-20
    ↓
AI Content Generation (Groq):
  - intro, why_like, best_for
  - pros, cons, specs
  - verdict, faq, final
    ↓
Image Transformation (avoid copyright):
  - Download original Amazon image
  - Apply effect: oil painting / watercolor / soft glow / vintage
  - Add drop shadow for professional look
  - Upload to free hosting (ImgBB/Telegraph/Catbox)
    ↓
Build HTML Article (Premium Template)
    ↓
Publish to Blogger + Telegram + Pinterest
```

### Batch Mode (/batch)
```
User sends .txt file with keywords (one per line)
    ↓
For EACH keyword:
  ├→ Search Amazon (top 3 results)
  ├→ Get ONE best product (highest value score)
  ├→ Transform image (random effect)
  ├→ Generate AI description
  └→ Publish to Blogger
    ↓
Progress updates every 5 products
    ↓
Final summary (success/failed count)
```

---

## 8. ARTICLE TEMPLATE (NESTDEAL CLEAN DESIGN)

The template follows a clean, conversion-focused design philosophy:

**Product → Quick Verdict → Key Specs → Pros/Cons → Why Buy → Amazon CTA → Details → FAQ**

### Design System:
- **Background:** #F7F8FA
- **Cards:** White with subtle borders
- **Text:** #172033
- **Primary CTA:** Amazon orange #FF9900
- **Secondary accent:** Dark navy #172033
- **Borders:** #E5E7EB
- **Border radius:** 12-16px
- **Shadow:** Very subtle

### Sections (in order):
1. **Breadcrumbs** — Home > Category > Product
2. **Hero Section** — Title, Rating, Tags, Image + Quick Take card
3. **Why It Stands Out** — 4-column feature grid with icons
4. **Pros & Cons** — Side-by-side green/red cards
5. **Is It Right for You?** — YES if / LOOK ELSEWHERE if
6. **Product Specifications** — Clean two-column table
7. **What You Should Know** — 3 numbered key points
8. **FAQ** — Accordion-style expandable questions
9. **Final CTA** — Dark section with affiliate button
10. **Disclosure** — Clean footer
11. **Sticky Mobile CTA** — Fixed bottom bar on mobile

### Key Principles:
- No excessive gradients
- No excessive animations
- No long paragraphs
- No multiple Amazon buttons in every line
- Use whitespace, cards, icons, bullet points
- Mobile-first responsive design
- Conversion-focused

---

## 9. KEY FILES

### `blogger_api_publisher.py`
- `_ai_generate_content()` — Generates AI content via Groq/OpenRouter
- `_build_article()` — Builds premium HTML article
- `_validate_article()` — Validates before publishing
- `publish_post()` — Publishes to Blogger API
- `_map_labels()` — Maps categories to Blogger labels

### `scraper.py`
- `scrape_product()` — Main scraper function
- `_normalize_price_to_usd()` — Converts MAD/EUR/GBP to USD
- `build_affiliate_url()` — Adds `?tag=dazzledeals00-20`
- `is_store_or_brand_url()` — Detects Amazon Store pages

### `fix_published_articles.py`
- Regenerates ALL published articles with new template
- Extracts product data from existing articles (no re-scraping)
- Deletes old posts, publishes new ones

---

## 10. COMMAND HANDLER TABLE

| Command | Handler File | Loading Msg | Est. Time | Admin Only |
|---------|-------------|-------------|-----------|------------|
| `/start` | `cmd_help.py` | No | <1s | No |
| `/help` | `cmd_help.py` | No | <1s | No |
| `/discover` | `cmd_discover.py` | Yes | 30-120s+ | Yes |
| `/addurl` | `cmd_addurl.py` | Yes | 30-120s+ | Yes |
| `/batch` | `main.py` | Yes | 30s per keyword | Yes |
| `/testai` | `cmd_testai.py` | Yes | 3-15s | Yes |
| `/testimage` | `main.py` | Yes | 10-20s | Yes |
| `/health` | `cmd_health.py` | No | <1s | No |
| `/stats` | `cmd_stats.py` | No | <1s | No |
| `/telegramcheck` | `cmd_telegram_diag.py` | Yes | 5-15s | Yes |
| `/sendtest` | `cmd_telegram_diag.py` | Yes | 3-10s | Yes |

---

## 11. KNOWN ISSUES & NEXT STEPS

### Issues
1. Amazon scraper gets blocked after ~2 requests (CAPTCHA)
2. Groq AI sometimes returns truncated JSON (fixed by reducing max_tokens)
3. Blogger rate limiting (HTTP 429) when publishing too fast

### Completed
- **NestDeal Clean Article Template** — Premium conversion-focused design with:
  - Clean hero section with image + Quick Take card
  - "Why It Stands Out" feature grid
  - Pros & Cons cards
  - "Is It Right for You?" decision helper
  - Clean specifications table
  - "What You Should Know" key points
  - Accordion FAQ
  - Final CTA section
  - Sticky mobile CTA bar
  - Mobile-first responsive design
- AI content generation via Groq
- Price normalization (MAD → USD)
- Affiliate tag integration (dazzledeals00-20)
- **Image transformation** — transforms Amazon images to avoid copyright:
  - 5 effects: oil painting, watercolor, soft glow, vintage, sketch
  - Auto-selects random effect for each article
  - Uploads transformed image to free hosting (ImgBB/Telegraph/Catbox)
  - Configurable via `IMAGE_TRANSFORM_PRESET` and `IMAGE_TRANSFORM_ENABLED`
- **Batch publishing** — send .txt file with keywords (one per line):
  - Searches Amazon for each keyword
  - Gets ONE best product per keyword
  - Transforms image automatically
  - Publishes to Blogger
  - Progress updates every 5 products
  - Command: `/batch` then send .txt file

### Next Steps
1. Fix remaining 14 articles that failed (Blogger rate limit)
2. Add more product sources (not just Amazon)
3. Add Pinterest integration
4. Add WordPress publishing option
5. Improve scraper anti-detection

---

## 12. TESTING

```powershell
# Run tests
python -m pytest tests/ -v

# Test template generation
python -c "from blogger_api_publisher import _build_article; print('OK')"

# Test scraper
python -c "import scraper; print(scraper.scrape_product('https://www.amazon.com/dp/B09XYZ123'))"

# Test Blogger connection
python -c "import blogger_api_publisher; print(blogger_api_publisher.test_connection())"
```

---

## 13. DEPLOYMENT

### Local (Current)
```powershell
python main.py
```


### Alternative: Railway
- Not recommended (billing issues)
- Old instance still running with old token

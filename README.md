# NestDeal — Amazon Affiliate Review Bot

Fully automated product review platform: discover Amazon products, generate strict AI reviews
with real buyer feedback, publish premium articles to Blogger, and notify Telegram — with
or without a PC (Koyeb cloud).

- **Blog:** https://nestdeal.blogspot.com/
- **Runtime:** Python 3.13+ · python-telegram-bot · Flask · Gunicorn (Docker)
- **Affiliate tag:** `dazzledeals00-20` (all Amazon links)

---

## 1. What it does

```
Amazon URL / keyword
  → 5-layer scrape (Desktop → Mobile → AW → Jina AI → Apify)
  → title, USD price, rating, review count, features, images, buyer reviews
  → enrichment: Amazon critical reviews + Reddit community feedback (auto, free)
  → Groq AI review (strict honesty: bad products get told so + Skip verdict)
  → premium HTML article (scores, sidebar, alternatives, comparison, FAQ, JSON-LD)
  → Blogger publish + Telegram channel alert + Pinterest
```

---

## 2. Article template (review standard)

Every article is a full review page:

| Block | Content |
|---|---|
| Hero | 1 featured picture, stars + rating count, Editor's Score /10, price, discount, Amazon CTA |
| Table of contents | Anchor links (Scores, Pros & Cons, Specs, Alternatives, Comparison, FAQ, Verdict) |
| Our Scores | 4 criteria bars (Value, Build, Features, Ease) + final score circle + We Recommend / Skip badge |
| Pros & Cons | Cons grounded in real buyer complaints, symbols sanitized |
| Analysis / Who / Specs / FAQ | AI content, all text sanitized |
| Top Alternatives | 2 real competing products with Amazon affiliate links |
| Head-to-Head Comparison | Reviewed product vs alternatives table |
| Sticky sidebar (PC) | Buy box, mini scores, more picks, key specs, trust card |
| SEO | JSON-LD Product+Review schema, `data-nd-review` span feeds theme sidebar/sticky CTA |
| Layout | Full-width 1160px PC, responsive mobile |

---

## 3. Bot commands

| Command | Effect |
|---|---|
| `/discover [url] [n]` | Discover & publish n products |
| `/addurl <url>` | Process one Amazon URL |
| `/batch` | Publish from .txt keywords file |
| `/testai` · `/testimage` | Test AI / image pipeline |
| `/telegramcheck` · `/sendtest` | Test channel connection |
| `/health` · `/stats` | Service status |
| `/start` · `/help` | Welcome / help |

Send any Amazon URL directly — the bot auto-processes it.

---

## 4. Auto-posting (live, not drafts)

- `daily_scheduler.py` runs sessions automatically: mixes niches, max 10 articles/day,
  ASIN dedup, 8s between posts, Telegram notifications per success/failure.
- Local `.env`: `AUTO_DISCOVER_ENABLED=true` (a session starts immediately on boot).
- Scheduler publishes **live** (`publish_now=True`) — drafts only when explicitly requested.

---

## 5. Setup — local (Windows)

```powershell
cd C:\Users\user\Desktop\bot-working
C:\Python314\python.exe main.py
# or double-click: start_auto_post.bat
```

Dashboard + bot polling start together. Keep the window open and the PC on.

## 6. Setup — cloud (Koyeb, PC off)

1. Push to GitHub (Koyeb auto-deploys `Dockerfile` → `gunicorn main:flask_app`, port 8080).
2. In Koyeb service settings, set **all** variables from `.env.example` with your real values
   (`BOT_TOKEN`, `BLOGGER_*`, `GROQ_API_KEY`, `AFFILIATE_TAG`, `AUTO_DISCOVER_ENABLED=true`, …).
3. `.env` is never committed — secrets live only in Koyeb env settings.

---

## 7. Environment variables

See `.env.example` for the full list. Key groups:

| Group | Vars |
|---|---|
| Telegram | `BOT_TOKEN`, `ADMIN_CHAT_ID`, `CHANNEL_ID` |
| Blogger API | `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`, `BLOGGER_BLOG_ID` |
| AI | `GROQ_API_KEY` (+ optional `OPENROUTER_API_KEY`) |
| Affiliate | `AFFILIATE_TAG=dazzledeals00-20` |
| Auto-post | `AUTO_DISCOVER_ENABLED`, `AUTO_DISCOVER_INTERVAL_HOURS` (4), `AUTO_ARTICLES_PER_RUN` (3), `AUTO_DAILY_MAX` (10), `AUTO_KEYWORDS` (JSON, optional) |
| Server | `PORT=8080`, `PUBLIC_DOMAIN` (empty = polling) |

---

## 8. Project structure

```
bot-working/
├── main.py                  # Entry: Flask + Telegram polling + schedulers
├── start_auto_post.bat      # One-click local launcher
├── daily_scheduler.py       # Auto-discover sessions, daily limits, Telegram reports
├── scheduler.py             # Telegram/Pinterest schedulers
├── scraper.py               # 5-layer Amazon scraper (USD prices, affiliate URLs, reviews)
├── review_enrichment.py     # Amazon critical reviews + Reddit feedback (free, auto)
├── content_generator.py     # AI product descriptions
├── blogger_api_publisher.py # Review template + Blogger API v3 publish + Telegram alert
├── blogger_publisher.py     # Backward-compat wrapper
├── config.py / core/config.py
├── core/db.py / core/cache.py  # SQLite + file cache (no cloud services)
├── handlers/                # /start /discover /addurl /testai /health /stats /sources
├── dashboard/               # Flask web dashboard
├── image_processor.py / image_transformer.py
├── wordpress_publisher.py / pinterest_api / pins.py
├── fix_published_articles.py# Bulk re-publish with current template
├── publish_pages.py / fix_pages.py / update_pages_banner.py
├── remove_page_breadcrumbs.py
├── Dockerfile / requirements.txt / .env.example
└── data/cache/              # Local file cache
```

---

## 9. Key behaviors & safeguards

- Prices always normalized to USD (`$`); MAD/EUR/GBP converted.
- Every Amazon URL gets `tag=dazzledeals00-20` (+ `rel="nofollow sponsored noopener"`).
- AI text sanitized: entities decoded, leading symbols stripped (theme/SVG provides icons),
  quotes escaped — no `&#10003;` ever visible, no broken markup.
- Blogger 429 rate limits: exponential backoff + min 3s between calls, never crashes.
- Strict reviews: repeated real complaints force cons, low scores, and Skip verdicts.
- Daily caps + ASIN dedup prevent spam and duplicates.

---

## 10. Testing

```powershell
# Blogger connection
C:\Python314\python.exe -c "from dotenv import load_dotenv; load_dotenv(); import blogger_api_publisher as b; print(b.is_configured(), b.test_connection())"

# Template build check
C:\Python314\python.exe -c "from dotenv import load_dotenv; load_dotenv(); import blogger_api_publisher as b; print(b._build_article({'title':'T','price':'$1','rating':4,'review_count':10,'features':[],'img_url':'','clean_url':'#','category':'x'}, 'd')[0])"
```

Draft tests publish with `publish_now=False` — check Blogger → Posts → Drafts.

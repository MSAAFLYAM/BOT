---
title: Amazon Bot Pin
emoji: 🤖
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# 🤖 Amazon Affiliate Bot

Fully automated Amazon product review platform that scrapes products, generates AI content, transforms images to avoid copyright, and publishes to Blogger.

## ✨ Features

- **Smart Scraping** — 5-layer fallback: Desktop → Mobile → AW → Jina AI → Apify
- **AI Content** — Generates Pros, Cons, Specs, FAQ, Verdict via Groq (free)
- **Image Transformation** — Oil painting, watercolor, vintage effects to avoid copyright
- **Batch Publishing** — Send .txt file with keywords, get 1 product per keyword
- **Affiliate Links** — Auto-attaches your Amazon affiliate tag
- **Multi-Platform** — Publishes to Blogger, Telegram, Pinterest

## 🚀 Quick Start

### 1. Clone & Install
```bash
git clone <your-repo-url>
cd bot-working
pip install -r requirements.txt
```

### 2. Configure `.env`
```bash
# Required
BOT_TOKEN=your_telegram_bot_token
CHANNEL_ID=@your_channel
ADMIN_CHAT_ID=your_telegram_id

# Blogger API
BLOGGER_CLIENT_ID=your_client_id
BLOGGER_CLIENT_SECRET=your_secret
BLOGGER_REFRESH_TOKEN=your_refresh_token
BLOG_ID=your_blog_id

# AI (free tier)
GROQ_API_KEY=your_groq_key

# Affiliate
AFFILIATE_TAG=yourtag-20

# Image Transformation
IMAGE_TRANSFORM_PRESET=auto
IMAGE_TRANSFORM_ENABLED=1
```

### 3. Run
```bash
python main.py
```

## 📋 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Show all commands |
| `/discover [url] [n]` | Discover & publish n products |
| `/addurl <url>` | Process single Amazon URL |
| `/batch` | Batch publish from .txt file |
| `/testimage [url]` | Test image transformation |
| `/health` | Check all services |
| `/testai` | Test AI providers |

## 📁 Project Structure

```
bot-working/
├── main.py                 # Entry point
├── scraper.py              # Amazon scraper (5-layer fallback)
├── blogger_api_publisher.py # Blogger publisher + article builder
├── image_transformer.py    # Image effects (oil, watercolor, etc.)
├── image_processor.py      # Image upload (ImgBB/Telegraph/Catbox)
├── content_generator.py    # AI description generator
├── config.py               # Environment variables
└── sample_keywords.txt     # Example batch file
```

## 🎨 Image Transformation

To avoid copyright detection, all product images are automatically transformed:

- **Oil Painting** — Brush stroke effect
- **Watercolor** — Soft painting effect
- **Soft Glow** — Professional bloom
- **Vintage** — Retro filter
- **Sketch** — Pencil drawing effect

Configure in `.env`:
```bash
IMAGE_TRANSFORM_PRESET=auto  # or oil_painting, watercolor, etc.
IMAGE_TRANSFORM_ENABLED=1    # set to 0 to disable
```

## 📋 Batch Publishing

1. Send `/batch` to the bot
2. Send a .txt file with keywords (one per line):
```
wireless earbuds
phone case iphone 15
usb c hub
laptop stand
```

Bot will:
- Search Amazon for each keyword
- Get ONE best product per keyword
- Transform image automatically
- Publish to Blogger

## ⚙️ Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram bot token |
| `CHANNEL_ID` | ✅ | Telegram channel ID |
| `ADMIN_CHAT_ID` | ✅ | Your Telegram user ID |
| `BLOGGER_CLIENT_ID` | ✅ | Google OAuth client ID |
| `BLOGGER_CLIENT_SECRET` | ✅ | Google OAuth secret |
| `BLOGGER_REFRESH_TOKEN` | ✅ | Google OAuth refresh token |
| `BLOG_ID` | ✅ | Blogger blog ID |
| `GROQ_API_KEY` | ✅ | Groq API key (free) |
| `AFFILIATE_TAG` | ✅ | Amazon affiliate tag |
| `IMAGE_TRANSFORM_PRESET` | ❌ | auto/oil_painting/watercolor/vintage/soft_glow |
| `IMAGE_TRANSFORM_ENABLED` | ❌ | 1=enabled, 0=disabled |
| `IMGBB_API_KEY` | ❌ | ImgBB API key for image hosting |

## 🛠 Tech Stack

- **Python 3.13**
- **python-telegram-bot** — Telegram integration
- **Flask** — Web dashboard
- **Groq** — Free AI content generation
- **BeautifulSoup** — Web scraping
- **Pillow** — Image transformation
- **Blogger API v3** — Publishing

## 📄 License

MIT

"""
publishing/blogger.py — Async Blogger API publisher.
publishing/telegram.py — Async Telegram channel publisher.
publishing/whatsapp.py — Async WhatsApp (Evolution API) publisher.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# BLOGGER
# ══════════════════════════════════════════════════════════════════

@dataclass
class BloggerResult:
    success:  bool
    post_id:  Optional[str] = None
    post_url: Optional[str] = None
    error:    Optional[str] = None


class BloggerPublisher:
    """
    Async Blogger publisher via Google Blogger API v3.

    Blogger doesn't have a media upload API — images are embedded
    directly in post HTML via <img src="..."> tags.
    TinyPNG compressed URL is used directly in the HTML.
    """

    API_BASE = "https://www.googleapis.com/blogger/v3/blogs"

    def __init__(self):
        import os
        self._blog_id       = os.environ.get("BLOGGER_BLOG_ID","")
        self._client_id     = os.environ.get("BLOGGER_CLIENT_ID","")
        self._client_secret = os.environ.get("BLOGGER_CLIENT_SECRET","")
        self._refresh_token = os.environ.get("BLOGGER_REFRESH_TOKEN","")
        self._access_token: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return bool(
            self._blog_id and self._client_id
            and self._client_secret and self._refresh_token
        )

    async def _get_access_token(self) -> Optional[str]:
        """Refresh OAuth2 access token."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type":    "refresh_token",
                        "client_id":     self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token,
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("access_token")
        except Exception as e:
            logger.warning(f"[blogger] Token refresh failed: {e}")
        return None

    async def publish_article(
        self,
        title:          str,
        html_content:   str,
        image_url:      str       = "",
        labels:         list[str] = None,
        compress_image: bool      = True,
        status:         str       = "LIVE",
    ) -> BloggerResult:
        """
        Publish article to Blogger.

        Image is embedded in HTML directly (Blogger doesn't have
        a separate media upload API like WordPress).
        TinyPNG compressed image URL is used for <img> src.
        """
        if not self.is_configured:
            return BloggerResult(False, error="Blogger not configured")

        # Get access token
        token = await self._get_access_token()
        if not token:
            return BloggerResult(False, error="Could not get Blogger access token")

        # Compress image URL for embedding
        final_image_url = image_url
        if image_url and compress_image:
            from publishing.image import get_tinify_client
            tinify = get_tinify_client()
            img_result = await tinify.compress_from_url(image_url, download_result=False)
            final_image_url = img_result.best_url

        # Add featured image at top of content if provided
        content = html_content
        if final_image_url:
            img_tag = (
                f'<div style="text-align:center;margin-bottom:20px;">'
                f'<img src="{final_image_url}" alt="{title}" '
                f'style="max-width:100%;height:auto;border-radius:8px;"/>'
                f'</div>\n'
            )
            content = img_tag + content

        post_data = {
            "title":   title,
            "content": content,
            "labels":  labels or [],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            url    = f"{self.API_BASE}/{self._blog_id}/posts"
            params = {"isDraft": "false" if status == "LIVE" else "true"}

            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=post_data,
                params=params,
            )

            if resp.status_code in (200, 201):
                post = resp.json()
                logger.info(f"[blogger] ✅ Published: {title[:60]}")
                return BloggerResult(
                    success=True,
                    post_id=post.get("id"),
                    post_url=post.get("url"),
                )

            error = f"HTTP {resp.status_code}: {resp.text[:150]}"
            logger.error(f"[blogger] ❌ Failed: {error}")
            return BloggerResult(False, error=error)


# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════

@dataclass
class TelegramResult:
    success:    bool
    message_id: Optional[int] = None
    error:      Optional[str] = None


class TelegramPublisher:
    """
    Async Telegram channel publisher.

    Sends formatted message with image to Telegram channel.
    Image is sent via sendPhoto (compressed URL or original).
    Caption includes title, excerpt, and article link.
    """

    API_BASE = "https://api.telegram.org/bot"

    def __init__(self):
        import os
        self._token      = os.environ.get("BOT_TOKEN","")
        self._channel_id = os.environ.get("CHANNEL_ID","")

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._channel_id)

    async def publish_article(
        self,
        title:            str,
        excerpt:          str          = "",
        image_url:        str          = "",
        article_url:      str          = "",
        affiliate_url:    str          = "",
        tags:             list[str]    = None,
        compress_image:   bool         = True,
        parse_mode:       str          = "HTML",
    ) -> TelegramResult:
        """
        Publish article to Telegram channel.

        Sends photo + caption with:
          - Title (bold)
          - Short excerpt (2-3 lines)
          - Tags as hashtags
          - Read more link
          - Buy link (if affiliate)
        """
        if not self.is_configured:
            return TelegramResult(False, error="Telegram not configured")

        # Build caption
        caption = f"<b>{title}</b>\n\n"
        if excerpt:
            # First 2 sentences only
            sentences = [s.strip() for s in excerpt.split('.') if s.strip()]
            short = '. '.join(sentences[:2])
            if short:
                caption += f"{short[:200]}\n\n"
        if tags:
            hashtags = " ".join(f"#{t.replace(' ','')}" for t in tags[:5])
            caption += f"{hashtags}\n"
        if article_url:
            caption += f"\n🔗 <a href='{article_url}'>Lire l'article complet</a>"
        if affiliate_url:
            caption += f"\n🛒 <a href='{affiliate_url}'>Voir sur Amazon</a>"

        # Compress image
        final_image_url = image_url
        if image_url and compress_image:
            from publishing.image import get_tinify_client
            tinify = get_tinify_client()
            img_result = await tinify.compress_from_url(image_url, download_result=False)
            final_image_url = img_result.best_url

        async with httpx.AsyncClient(timeout=30) as client:
            base = f"{self.API_BASE}{self._token}"

            if final_image_url:
                resp = await client.post(
                    f"{base}/sendPhoto",
                    json={
                        "chat_id":    self._channel_id,
                        "photo":      final_image_url,
                        "caption":    caption[:1024],
                        "parse_mode": parse_mode,
                    },
                )
            else:
                resp = await client.post(
                    f"{base}/sendMessage",
                    json={
                        "chat_id":    self._channel_id,
                        "text":       caption[:4096],
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": False,
                    },
                )

            if resp.status_code == 200:
                data = resp.json()
                msg  = data.get("result", {})
                logger.info(f"[telegram] ✅ Published: {title[:60]}")
                return TelegramResult(
                    success=True,
                    message_id=msg.get("message_id"),
                )

            error = f"HTTP {resp.status_code}: {resp.text[:150]}"
            logger.error(f"[telegram] ❌ Failed: {error}")
            return TelegramResult(False, error=error)


# ══════════════════════════════════════════════════════════════════
# WHATSAPP
# ══════════════════════════════════════════════════════════════════

@dataclass
class WhatsAppResult:
    success:    bool
    message_id: Optional[str] = None
    error:      Optional[str] = None


class WhatsAppPublisher:
    """
    Async WhatsApp publisher via Evolution API.

    Sends image + text message to WhatsApp channel.
    """

    def __init__(self):
        import os
        self._api_url  = (os.environ.get("EVOLUTION_API_URL","") or "").rstrip("/")
        self._api_key  = os.environ.get("EVOLUTION_API_KEY","")
        self._instance = os.environ.get("EVOLUTION_INSTANCE","amazonbot")
        self._channel  = os.environ.get("WA_CHANNEL_NUMBER","")

    @property
    def is_configured(self) -> bool:
        return bool(self._api_url and self._api_key and self._instance and self._channel)

    async def publish_article(
        self,
        title:          str,
        excerpt:        str        = "",
        image_url:      str        = "",
        article_url:    str        = "",
        affiliate_url:  str        = "",
        compress_image: bool       = True,
    ) -> WhatsAppResult:
        """Send article to WhatsApp channel via Evolution API."""
        if not self.is_configured:
            return WhatsAppResult(False, error="WhatsApp not configured")

        # Compress image
        final_image_url = image_url
        if image_url and compress_image:
            from publishing.image import get_tinify_client
            tinify = get_tinify_client()
            img_result = await tinify.compress_from_url(image_url, download_result=False)
            final_image_url = img_result.best_url

        # Build message text
        text = f"*{title}*\n\n"
        if excerpt:
            sentences = [s.strip() for s in excerpt.split('.') if s.strip()]
            text += '. '.join(sentences[:2]) + '\n\n'
        if article_url:
            text += f"📖 Lire: {article_url}\n"
        if affiliate_url:
            text += f"🛒 Amazon: {affiliate_url}"

        headers = {
            "apikey":       self._api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                if final_image_url:
                    resp = await client.post(
                        f"{self._api_url}/message/sendMedia/{self._instance}",
                        headers=headers,
                        json={
                            "number":    self._channel,
                            "mediatype": "image",
                            "mimetype":  "image/jpeg",
                            "media":     final_image_url,
                            "caption":   text[:1000],
                        },
                    )
                else:
                    resp = await client.post(
                        f"{self._api_url}/message/sendText/{self._instance}",
                        headers=headers,
                        json={
                            "number": self._channel,
                            "text":   text[:2000],
                        },
                    )

                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"[whatsapp] ✅ Sent: {title[:60]}")
                    return WhatsAppResult(
                        success=True,
                        message_id=str(data.get("key", {}).get("id", "")),
                    )

                error = f"HTTP {resp.status_code}: {resp.text[:100]}"
                logger.error(f"[whatsapp] ❌ Failed: {error}")
                return WhatsAppResult(False, error=error)

            except Exception as e:
                logger.error(f"[whatsapp] Exception: {e}")
                return WhatsAppResult(False, error=str(e)[:100])

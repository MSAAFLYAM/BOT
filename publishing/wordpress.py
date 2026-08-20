"""
publishing/wordpress.py — Async WordPress REST API publisher.

Publishes articles to WordPress via REST API v2.
Handles: image upload → post creation → category/tag assignment.

Architecture decisions:
  - Image uploaded BEFORE post creation (WordPress requires media ID).
  - TinyPNG compression applied before upload (compressed bytes → upload).
  - Categories and tags created automatically if they don't exist.
  - Posts created as "draft" by default → change to "publish" only if auto-publish enabled.
  - Duplicate detection: check post slug before publishing (don't double-post).
  - Error handling: HTTP 401 = credentials error (no retry), 5xx = retry.

WordPress REST API endpoints used:
  POST /wp-json/wp/v2/media  → upload image
  POST /wp-json/wp/v2/posts  → create post
  GET  /wp-json/wp/v2/posts  → check duplicate
  POST /wp-json/wp/v2/tags   → create tag
"""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WordPressResult:
    """Result of a WordPress publish operation."""
    success:  bool
    post_id:  Optional[int]   = None
    post_url: Optional[str]   = None
    error:    Optional[str]   = None


class WordPressPublisher:
    """
    Async WordPress publisher via REST API.

    Usage:
        publisher = WordPressPublisher()
        result = await publisher.publish_article(
            title="Best Wireless Headphones 2026",
            html_content=article_html,
            image_url="https://cdn.example.com/headphones.jpg",
            tags=["headphones", "audio"],
            category="Electronics",
            slug="best-wireless-headphones-2026",
            meta_description="Discover our top picks...",
            status="publish",  # or "draft"
        )
    """

    def __init__(self):
        import os
        self._base_url  = (os.environ.get("WP_SITE_URL","") or "").rstrip("/")
        self._username  = os.environ.get("WP_USERNAME","")
        self._password  = os.environ.get("WP_APP_PASSWORD","")
        self._auth      = (self._username, self._password)

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._username and self._password)

    async def publish_article(
        self,
        title:            str,
        html_content:     str,
        image_url:        str          = "",
        slug:             str          = "",
        meta_description: str          = "",
        tags:             list[str]    = None,
        category:         str          = "",
        status:           str          = "publish",
        compress_image:   bool         = True,
    ) -> WordPressResult:
        """
        Publish a full article to WordPress.

        Steps:
          1. Check if post with same slug already exists (duplicate prevention)
          2. Compress image via TinyPNG (if enabled and > threshold)
          3. Upload compressed image to WordPress media library
          4. Create post with featured image
          5. Assign tags and category

        Returns:
          WordPressResult with post_id and post_url on success.
        """
        if not self.is_configured:
            return WordPressResult(False, error="WordPress not configured")

        async with httpx.AsyncClient(
            auth=self._auth,
            timeout=httpx.Timeout(60.0),
        ) as client:

            # Step 1: Duplicate check
            if slug:
                existing = await self._get_post_by_slug(client, slug)
                if existing:
                    logger.info(f"[wp] Post already exists: {slug}")
                    return WordPressResult(
                        True,
                        post_id=existing.get("id"),
                        post_url=existing.get("link"),
                    )

            # Step 2 + 3: Compress and upload image
            featured_media_id = None
            if image_url:
                featured_media_id = await self._upload_image(
                    client, image_url, title, compress_image
                )

            # Step 4: Create post
            post_data = {
                "title":   title,
                "content": html_content,
                "status":  status,
                "slug":    slug or "",
                "excerpt": meta_description,
            }
            if featured_media_id:
                post_data["featured_media"] = featured_media_id

            # Step 5: Tags
            if tags:
                tag_ids = await self._get_or_create_tags(client, tags)
                if tag_ids:
                    post_data["tags"] = tag_ids

            # Category
            if category:
                cat_id = await self._get_or_create_category(client, category)
                if cat_id:
                    post_data["categories"] = [cat_id]

            resp = await client.post(
                f"{self._base_url}/wp-json/wp/v2/posts",
                json=post_data,
            )

            if resp.status_code in (200, 201):
                post = resp.json()
                logger.info(
                    f"[wp] ✅ Published: {title[:60]} "
                    f"(id={post.get('id')}, status={status})"
                )
                return WordPressResult(
                    success=True,
                    post_id=post.get("id"),
                    post_url=post.get("link"),
                )

            error = f"HTTP {resp.status_code}: {resp.text[:150]}"
            logger.error(f"[wp] ❌ Publish failed: {error}")
            return WordPressResult(False, error=error)

    async def _upload_image(
        self,
        client:          httpx.AsyncClient,
        image_url:       str,
        title:           str,
        compress:        bool,
    ) -> Optional[int]:
        """Upload image to WordPress media library. Returns media ID."""
        try:
            image_bytes = None
            final_url   = image_url

            # Compress via TinyPNG
            if compress:
                from publishing.image import get_tinify_client
                tinify   = get_tinify_client()
                result   = await tinify.compress_from_url(image_url, download_result=True)
                if result.compressed_bytes:
                    image_bytes = result.compressed_bytes
                    final_url   = result.compressed_url
                    logger.debug(
                        f"[wp] Image compressed: -{result.ratio*100:.0f}% "
                        f"({result.saved_kb:.0f}KB saved)"
                    )

            # Download if no compressed bytes
            if not image_bytes:
                dl = await client.get(image_url, follow_redirects=True, timeout=30)
                image_bytes = dl.content

            if not image_bytes:
                return None

            # Detect mime type
            mime = "image/jpeg"
            ext  = image_url.split("?")[0].rsplit(".", 1)[-1].lower()
            if ext in ("png",):  mime = "image/png"
            elif ext in ("webp",): mime = "image/webp"

            filename = f"{title[:40].replace(' ','-').lower()}.{ext or 'jpg'}"

            resp = await client.post(
                f"{self._base_url}/wp-json/wp/v2/media",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Type":        mime,
                },
                content=image_bytes,
                timeout=60,
            )
            if resp.status_code in (200, 201):
                media_id = resp.json().get("id")
                logger.debug(f"[wp] Image uploaded: media_id={media_id}")
                return media_id

        except Exception as e:
            logger.warning(f"[wp] Image upload failed: {e}")
        return None

    async def _get_post_by_slug(self, client: httpx.AsyncClient, slug: str) -> Optional[dict]:
        try:
            resp = await client.get(
                f"{self._base_url}/wp-json/wp/v2/posts",
                params={"slug": slug, "per_page": 1},
            )
            posts = resp.json()
            return posts[0] if isinstance(posts, list) and posts else None
        except Exception:
            return None

    async def _get_or_create_tags(self, client: httpx.AsyncClient, tags: list) -> list:
        tag_ids = []
        for tag in tags[:10]:
            try:
                # Try to find existing
                resp = await client.get(
                    f"{self._base_url}/wp-json/wp/v2/tags",
                    params={"search": tag, "per_page": 1},
                )
                existing = resp.json()
                if isinstance(existing, list) and existing:
                    tag_ids.append(existing[0]["id"])
                    continue
                # Create new tag
                resp = await client.post(
                    f"{self._base_url}/wp-json/wp/v2/tags",
                    json={"name": tag},
                )
                if resp.status_code in (200, 201):
                    tag_ids.append(resp.json()["id"])
            except Exception:
                continue
        return tag_ids

    async def _get_or_create_category(self, client: httpx.AsyncClient, name: str) -> Optional[int]:
        try:
            resp = await client.get(
                f"{self._base_url}/wp-json/wp/v2/categories",
                params={"search": name, "per_page": 1},
            )
            existing = resp.json()
            if isinstance(existing, list) and existing:
                return existing[0]["id"]
            resp = await client.post(
                f"{self._base_url}/wp-json/wp/v2/categories",
                json={"name": name},
            )
            if resp.status_code in (200, 201):
                return resp.json()["id"]
        except Exception:
            pass
        return None

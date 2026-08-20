"""
core/exceptions.py — Custom exception hierarchy.

Architecture decisions:
  - All application exceptions inherit from SaaSBaseException.
  - Each domain has its own exception class.
  - Exceptions carry structured context (not just a string message).
  - This allows centralized error handling in Celery tasks and API handlers.
  - Retry decisions are made based on exception TYPE, not message parsing.

Usage:
    raise ScrapingBlockedError(
        url="https://marmiton.org/...",
        method="playwright",
        status_code=403,
    )

    # In Celery task:
    except ScrapingBlockedError as e:
        if e.is_retryable:
            raise self.retry(exc=e, countdown=e.retry_after)
"""
from __future__ import annotations

from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────────────
#  Base Exception
# ──────────────────────────────────────────────────────────────────────────────

class SaaSBaseException(Exception):
    """
    Root exception for the entire application.

    All application-specific exceptions must inherit from this.
    This allows a single except clause to catch all app errors
    while still distinguishing them from stdlib/third-party errors.
    """

    # Can this error be resolved by retrying? Default False.
    is_retryable: bool = False

    # How many seconds to wait before retrying (for Celery countdown).
    retry_after: int = 30

    def __init__(
        self,
        message: str = "",
        context: dict[str, Any] | None = None,
        original_error: Exception | None = None,
    ):
        self.message = message
        self.context = context or {}
        self.original_error = original_error
        super().__init__(message)

    def to_dict(self) -> dict:
        """Serialize for storage in Job.error_detail (JSON column)."""
        return {
            "type":           type(self).__name__,
            "message":        self.message,
            "is_retryable":   self.is_retryable,
            "retry_after":    self.retry_after,
            "context":        self.context,
            "original_error": str(self.original_error) if self.original_error else None,
        }

    def __repr__(self) -> str:
        ctx = f", context={self.context}" if self.context else ""
        return f"{type(self).__name__}(message={self.message!r}{ctx})"


# ──────────────────────────────────────────────────────────────────────────────
#  Configuration Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class ConfigurationError(SaaSBaseException):
    """
    Raised when required configuration is missing or invalid.
    NOT retryable — requires human intervention.
    """
    is_retryable = False


class MissingCredentialsError(ConfigurationError):
    """API key or credential is missing."""

    def __init__(self, service: str, variable: str):
        super().__init__(
            message=f"Missing credentials for {service}. "
                    f"Set environment variable: {variable}",
            context={"service": service, "variable": variable},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Database Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class DatabaseError(SaaSBaseException):
    """Base database exception."""
    is_retryable = True
    retry_after = 5


class DatabaseConnectionError(DatabaseError):
    """Cannot connect to PostgreSQL."""

    def __init__(self, url_hint: str = "", original_error: Exception | None = None):
        super().__init__(
            message="Cannot connect to PostgreSQL database.",
            context={"url_hint": url_hint},
            original_error=original_error,
        )


class RecordNotFoundError(DatabaseError):
    """Requested record does not exist."""
    is_retryable = False

    def __init__(self, model: str, identifier: Any):
        super().__init__(
            message=f"{model} with id={identifier} not found.",
            context={"model": model, "identifier": str(identifier)},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Scraping Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class ScrapingError(SaaSBaseException):
    """Base scraping exception."""
    is_retryable = True
    retry_after = 60


class ScrapingBlockedError(ScrapingError):
    """
    Request was blocked by CDN, Cloudflare, or bot detection.

    This is the most common error when IPs are in a
    datacenter range that Cloudflare has flagged.

    Retry strategy: switch to a different scraping method,
    not just retry the same one.
    """
    retry_after = 120

    def __init__(
        self,
        url: str,
        method: str,
        status_code: int = 403,
        original_error: Exception | None = None,
    ):
        super().__init__(
            message=f"Scraping blocked by {url} using method={method}. "
                    f"HTTP {status_code}.",
            context={"url": url, "method": method, "status_code": status_code},
            original_error=original_error,
        )


class ScrapingTimeoutError(ScrapingError):
    """Request timed out."""
    retry_after = 30

    def __init__(self, url: str, timeout: int, method: str = ""):
        super().__init__(
            message=f"Scraping timeout after {timeout}s: {url}",
            context={"url": url, "timeout": timeout, "method": method},
        )


class ScrapingParseError(ScrapingError):
    """
    HTML was fetched but content extraction failed.
    Possible causes: site structure changed, unexpected HTML.
    """
    is_retryable = False  # Retry won't help — code fix needed

    def __init__(self, url: str, reason: str):
        super().__init__(
            message=f"Parse failed for {url}: {reason}",
            context={"url": url, "reason": reason},
        )


class ScrapingAllMethodsFailedError(ScrapingError):
    """
    All scraping methods exhausted, none succeeded.
    Logged as a critical failure for monitoring.
    """
    is_retryable = False
    retry_after = 0

    def __init__(self, url: str, methods_tried: list[str]):
        super().__init__(
            message=f"All scraping methods failed for {url}. "
                    f"Tried: {', '.join(methods_tried)}",
            context={"url": url, "methods_tried": methods_tried},
        )


class InvalidURLError(ScrapingError):
    """URL is malformed or not supported."""
    is_retryable = False

    def __init__(self, url: str, reason: str = ""):
        super().__init__(
            message=f"Invalid or unsupported URL: {url}. {reason}",
            context={"url": url, "reason": reason},
        )


class ASINNotFoundError(ScrapingError):
    """Cannot extract ASIN from Amazon URL."""
    is_retryable = False

    def __init__(self, url: str):
        super().__init__(
            message=f"Cannot extract ASIN from URL: {url}",
            context={"url": url},
        )


class ProductRejectedError(ScrapingError):
    """
    Product was successfully scraped but rejected by quality filters.
    (low rating, no reviews, unavailable, etc.)
    """
    is_retryable = False

    def __init__(self, asin: str, reason: str, values: dict | None = None):
        super().__init__(
            message=f"Product {asin} rejected: {reason}",
            context={"asin": asin, "reason": reason, "values": values or {}},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  AI / Content Generation Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class AIError(SaaSBaseException):
    """Base AI generation exception."""
    is_retryable = True
    retry_after = 30


class AIServiceUnavailableError(AIError):
    """OpenRouter or LLM service is down or rate limited."""
    retry_after = 60

    def __init__(self, service: str = "OpenRouter", status_code: int = 0):
        super().__init__(
            message=f"AI service {service} unavailable (HTTP {status_code}).",
            context={"service": service, "status_code": status_code},
        )


class AIQualityTooLowError(AIError):
    """Generated content quality score is below threshold."""
    is_retryable = True  # Retry with different prompt/temperature
    retry_after = 5

    def __init__(self, score: int, threshold: int, content_preview: str = ""):
        super().__init__(
            message=f"AI quality score {score} below threshold {threshold}.",
            context={
                "score": score,
                "threshold": threshold,
                "content_preview": content_preview[:200],
            },
        )


class AIResponseParseError(AIError):
    """AI returned unexpected format."""
    is_retryable = True
    retry_after = 10

    def __init__(self, expected: str, received: str):
        super().__init__(
            message=f"AI response parse failed. Expected: {expected}",
            context={"expected": expected, "received": received[:200]},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Publishing Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class PublishingError(SaaSBaseException):
    """Base publishing exception."""
    is_retryable = True
    retry_after = 60


class WordPressPublishError(PublishingError):
    """WordPress publishing failed."""

    def __init__(self, reason: str, status_code: int = 0):
        super().__init__(
            message=f"WordPress publish failed: {reason}",
            context={"reason": reason, "status_code": status_code},
        )


class BloggerPublishError(PublishingError):
    """Blogger publishing failed."""

    def __init__(self, reason: str, status_code: int = 0):
        super().__init__(
            message=f"Blogger publish failed: {reason}",
            context={"reason": reason, "status_code": status_code},
        )


class TelegramPublishError(PublishingError):
    """Telegram channel publishing failed."""
    retry_after = 30

    def __init__(self, reason: str, chat_id: str = ""):
        super().__init__(
            message=f"Telegram publish failed: {reason}",
            context={"reason": reason, "chat_id": chat_id},
        )


class PinterestExportError(PublishingError):
    """Pinterest CSV export or API publishing failed."""

    def __init__(self, reason: str, row_count: int = 0):
        super().__init__(
            message=f"Pinterest export failed: {reason}",
            context={"reason": reason, "row_count": row_count},
        )


class WhatsAppPublishError(PublishingError):
    """WhatsApp publishing via Evolution API failed."""
    retry_after = 30

    def __init__(self, reason: str, channel: str = ""):
        super().__init__(
            message=f"WhatsApp publish failed: {reason}",
            context={"reason": reason, "channel": channel},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Image Pipeline Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class ImageError(SaaSBaseException):
    """Base image pipeline exception."""
    is_retryable = True
    retry_after = 30


class ImageDownloadError(ImageError):
    """Cannot download image from URL."""

    def __init__(self, url: str, status_code: int = 0):
        super().__init__(
            message=f"Image download failed from {url} (HTTP {status_code}).",
            context={"url": url, "status_code": status_code},
        )


class ImageUploadError(ImageError):
    """Cannot upload image to hosting service."""

    def __init__(self, service: str, reason: str):
        super().__init__(
            message=f"Image upload to {service} failed: {reason}",
            context={"service": service, "reason": reason},
        )


class CanvasRenderError(ImageError):
    """Pillow canvas rendering failed."""
    is_retryable = False

    def __init__(self, template: str, reason: str):
        super().__init__(
            message=f"Canvas render failed for template={template}: {reason}",
            context={"template": template, "reason": reason},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Queue / Job Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class JobError(SaaSBaseException):
    """Base job/queue exception."""
    is_retryable = True
    retry_after = 10


class JobMaxRetriesExceededError(JobError):
    """Job has exceeded its maximum retry count."""
    is_retryable = False

    def __init__(self, job_id: str, job_type: str, retries: int):
        super().__init__(
            message=f"Job {job_id} ({job_type}) exceeded max retries ({retries}).",
            context={"job_id": job_id, "job_type": job_type, "retries": retries},
        )


class JobNotFoundError(JobError):
    """Job ID not found in database."""
    is_retryable = False

    def __init__(self, job_id: str):
        super().__init__(
            message=f"Job {job_id} not found.",
            context={"job_id": job_id},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Google Sheets Exceptions (operational layer only)
# ──────────────────────────────────────────────────────────────────────────────

class SheetsError(SaaSBaseException):
    """
    Google Sheets operational layer exception.

    NOTE: Sheets is not source of truth. Sheets errors should
    never block the main pipeline — log and continue.
    """
    is_retryable = True
    retry_after = 30


class SheetsQuotaError(SheetsError):
    """Google Sheets API quota exceeded (300 req/min)."""
    retry_after = 60

    def __init__(self):
        super().__init__(
            message="Google Sheets API quota exceeded. "
                    "Will retry after 60 seconds.",
        )

"""
api/webhook.py — Async Telegram webhook handler for FastAPI.

Architecture decisions:
  - FastAPI endpoint receives Telegram Update as JSON.
  - Update is put into an asyncio.Queue (non-blocking, returns 200 fast).
    Telegram requires response within 5 seconds — queue ensures this.
  - A background consumer task processes updates asynchronously.
  - Bot handlers are imported from the existing bot module.
  - Webhook secret verified in URL path (same as Flask version).

Why asyncio.Queue instead of direct processing:
  - Direct processing blocks the webhook endpoint if handler is slow.
  - Telegram marks requests as failed if no response within 5s.
  - Queue allows instant ACK + background processing.
  - Queue is in-memory (not Redis) — fast, no overhead for simple updates.

Telegram update types handled:
  - message          → command handlers (/start, /scrape, etc.)
  - callback_query   → inline keyboard buttons (approval flow)
  - channel_post     → channel messages (if bot is channel admin)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory queue for Telegram updates
_update_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
_consumer_task: Optional[asyncio.Task] = None


@router.post("/webhook/{secret}")
async def telegram_webhook(
    secret:            str,
    request:           Request,
    background_tasks:  BackgroundTasks,
) -> Response:
    """
    Receive Telegram Update via webhook.

    Validates secret, enqueues update, returns 200 immediately.
    Telegram requires response < 5s — queue ensures this.
    """
    import os
    expected_secret = os.environ.get("WEBHOOK_SECRET", "")

    if expected_secret and secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Non-blocking enqueue
    try:
        _update_queue.put_nowait(update_data)
    except asyncio.QueueFull:
        logger.warning("[webhook] Queue full, dropping update")

    # Return 200 immediately to Telegram
    return Response(status_code=200)


async def start_webhook_consumer():
    """
    Start background consumer that processes Telegram updates.

    Called from FastAPI startup event.
    Processes one update at a time from the queue.
    """
    global _consumer_task
    _consumer_task = asyncio.create_task(_consume_updates())
    logger.info("[webhook] Update consumer started")


async def stop_webhook_consumer():
    """Stop the update consumer gracefully."""
    global _consumer_task
    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
    logger.info("[webhook] Update consumer stopped")


async def _consume_updates():
    """
    Background consumer: dequeue and process Telegram updates.

    Runs forever until cancelled.
    Each update processed by the bot's dispatcher.
    Errors are caught and logged — consumer never dies from one bad update.
    """
    logger.info("[webhook] Consumer loop started")
    while True:
        try:
            update_data = await asyncio.wait_for(
                _update_queue.get(),
                timeout=30.0,
            )
            await _process_update(update_data)
            _update_queue.task_done()

        except asyncio.TimeoutError:
            continue  # No updates — keep waiting
        except asyncio.CancelledError:
            logger.info("[webhook] Consumer cancelled")
            break
        except Exception as e:
            logger.error(f"[webhook] Consumer error: {e}", exc_info=True)
            await asyncio.sleep(1)  # Brief pause on error


async def _process_update(update_data: dict) -> None:
    """
    Process a single Telegram update.

    Imports bot dispatcher and routes the update.
    Works with both python-telegram-bot v20+ (async) and pyTelegramBotAPI.
    """
    try:
        # Try python-telegram-bot v20+ first (async dispatcher)
        try:
            import telegram
            from telegram import Update

            # Build Update object
            update = Update.de_json(update_data, bot=None)

            # Get the bot application (imported from main module)
            try:
                from bot.dispatcher import get_application
                application = get_application()
                await application.process_update(update)
                return
            except ImportError:
                pass

        except ImportError:
            pass

        # Fallback: pyTelegramBotAPI (existing bot)
        # The existing main.py uses pyTelegramBotAPI
        # We call the existing handler directly
        try:
            import main as bot_main
            if hasattr(bot_main, "process_telegram_update"):
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    bot_main.process_telegram_update,
                    update_data,
                )
            else:
                # Fallback: put in existing queue if bot uses one
                if hasattr(bot_main, "_update_queue"):
                    from telegram import Update as TGUpdate
                    try:
                        update = TGUpdate.de_json(update_data, bot_main.bot)
                        bot_main._update_queue.put(update)
                    except Exception:
                        import queue
                        bot_main._update_queue.put(update_data)
        except Exception as e:
            logger.warning(f"[webhook] Bot processing error: {e}")

    except Exception as e:
        logger.error(f"[webhook] Update processing failed: {e}", exc_info=True)

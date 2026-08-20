# pinterest_publisher.py — Async Playwright Pinterest Automation
# ================================================================
# Architecture: Template Method Pattern
#   PinterestPublisher
#     ├── ensure_session()   → login or reuse saved session
#     ├── login()            → manual or automated login
#     ├── save_session()     → persist cookies/storage to JSON
#     ├── load_session()     → restore session from JSON
#     ├── create_pin()       → open pin creation UI
#     ├── publish_pin()      → fill form + submit
#     └── update_status()    → mark row as published in Sheets
#
# Anti-detection:
#   - Stealth script (disables navigator.webdriver)
#   - Realistic viewport + locale + timezone
#   - Human-like mouse movements + typing delays
#   - Random waits between actions
#
# Deployment compatibility:
#   - Headless Chromium (--no-sandbox)
#   - Low RAM flags
#   - Screenshot on error for debugging
# ================================================================

import asyncio
import json
import logging
import os
import random
import re
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeout,
)

# ── Project imports ───────────────────────────────────────────
try:
    import config
    import sheets_handler
    import queue_manager
except ImportError:
    config = None
    sheets_handler = None
    queue_manager  = None

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════

SESSION_FILE      = os.environ.get("PINTEREST_SESSION", "storage_state.json")
SCREENSHOTS_DIR   = "screenshots"
PINTEREST_EMAIL   = os.environ.get("PINTEREST_EMAIL", "")
PINTEREST_PASS    = os.environ.get("PINTEREST_PASS", "")
PINTEREST_TIMEOUT = int(os.environ.get("PINTEREST_TIMEOUT", "30000"))   # ms
MAX_RETRIES       = int(os.environ.get("PINTEREST_RETRIES", "3"))

# ── Human-like delay ranges (seconds) ────────────────────────
DELAY_SHORT  = (0.8, 1.8)    # between small actions
DELAY_MEDIUM = (2.0, 4.0)    # between major steps
DELAY_LONG   = (4.0, 8.0)    # after page loads

# ── Pinterest selectors (multiple fallbacks per element) ──────
SELECTORS = {
    "email_input": [
        'input[name="id"]',
        'input[type="email"]',
        '#email',
        'input[placeholder*="email" i]',
    ],
    "password_input": [
        'input[name="password"]',
        'input[type="password"]',
        '#password',
    ],
    "login_button": [
        'button[type="submit"]',
        '[data-test-id="registerFormSubmitButton"]',
        'button:has-text("Log in")',
        'button:has-text("Sign in")',
    ],
    "logged_in_proof": [
        '[data-test-id="header-profile"]',
        '[data-test-id="homefeedContentWrapper"]',
        'a[href*="/settings/"]',
        '[aria-label="Your profile"]',
        'div[data-test-id="pin-page"]',
    ],
    "create_button": [
        '[data-test-id="header-create-menu"]',
        'a[href="/pin-creation-tool/"]',
        '[aria-label="Create"]',
        'button:has-text("Create")',
        '[data-test-id="create-pin-button"]',
    ],
    "image_upload": [
        'input[type="file"]',
        '[data-test-id="media-upload"]',
        '[accept*="image"]',
    ],
    "title_input": [
        '[data-test-id="pin-draft-title"]',
        '[placeholder*="title" i]',
        '[aria-label*="Title" i]',
        'div[role="textbox"][aria-label*="title" i]',
        'textarea[placeholder*="title" i]',
        '[data-test-id="pin-builder-title-textarea"]',
    ],
    "description_input": [
        '[data-test-id="pin-draft-description"]',
        '[placeholder*="description" i]',
        '[aria-label*="description" i]',
        'div[role="textbox"][aria-label*="description" i]',
        '[data-test-id="pin-builder-description"]',
    ],
    "link_input": [
        '[data-test-id="pin-draft-link"]',
        '[placeholder*="link" i]',
        '[aria-label*="link" i]',
        'input[placeholder*="destinat" i]',
        '[data-test-id="pin-builder-link"]',
    ],
    "board_dropdown": [
        '[data-test-id="board-dropdown-select-btn"]',
        'button:has-text("Choose a board")',
        '[aria-label*="board" i]',
        '[data-test-id="board-selector"]',
    ],
    "board_search": [
        '[data-test-id="board-dropdown-search-input"]',
        'input[placeholder*="Search boards" i]',
        'input[placeholder*="Rechercher" i]',
        '[aria-label*="search board" i]',
    ],
    "board_option": [
        '[data-test-id="board-row"]',
        '[data-test-id="boardsListItem"]',
        'li[role="option"]',
    ],
    "create_board_btn": [
        '[data-test-id="create-board-button"]',
        'button:has-text("Create board")',
        'button:has-text("Créer un tableau")',
        'div:has-text("Create board")',
        'div:has-text("Créer un tableau")'
    ],
    "new_board_name_input": [
        'input[id="board-name"]',
        'input[placeholder*="Like" i]',
        'input[placeholder*="Exemple" i]',
        'input[type="text"]'
    ],
    "confirm_create_board": [
        '[data-test-id="create-board-submit-button"]',
        'button:has-text("Create")',
        'button:has-text("Créer")'
    ],
    "publish_button": [
        '[data-test-id="board-dropdown-save-button"]',
        'button:has-text("Publish")',
        'button:has-text("Save")',
        'button:has-text("Publier")',
        '[data-test-id="pin-draft-save-button"]',
    ],
    "pin_saved_toast": [
        '[data-test-id="toast-message"]',
        ':has-text("Pin saved")',
        ':has-text("published")',
        ':has-text("enregistrée")',
        '[data-test-id="success-toast"]',
    ],
}

# ════════════════════════════════════════════════════════════════
# STEALTH SCRIPT — disables bot detection signals
# ════════════════════════════════════════════════════════════════

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client', filename: 'internal-nacl-plugin' },
    ],
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : originalQuery(parameters)
);
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};
"""

# ── Helper utilities ───────────────────────────────────────────
async def _human_delay(range_: tuple = DELAY_SHORT):
    await asyncio.sleep(random.uniform(*range_))

async def _human_type(page: Page, selector: str, text: str):
    el = await page.wait_for_selector(selector, timeout=PINTEREST_TIMEOUT)
    await el.click()
    await _human_delay(DELAY_SHORT)
    await el.fill("")
    for char in text:
        await el.type(char, delay=random.randint(40, 130))
    await _human_delay(DELAY_SHORT)

async def _human_type_el(element, text: str):
    await element.click()
    await _human_delay(DELAY_SHORT)
    await element.fill("")
    for char in text:
        await element.type(char, delay=random.randint(40, 130))
    await _human_delay(DELAY_SHORT)

async def _click_first_matching(page: Page, selectors: list[str], timeout: int = 15000) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout)
            if el:
                await el.scroll_into_view_if_needed()
                await _human_delay(DELAY_SHORT)
                await el.click()
                return True
        except PlaywrightTimeout:
            continue
        except Exception:
            continue
    return False

async def _find_first(page: Page, selectors: list[str], timeout: int = 15000):
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout)
            if el:
                return el
        except PlaywrightTimeout:
            continue
        except Exception:
            continue
    return None

async def _screenshot(page: Page, name: str):
    try:
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SCREENSHOTS_DIR, f"{name}_{ts}.png")
        await page.screenshot(path=path, full_page=True)
        logger.info(f"[pinterest] Screenshot saved: {path}")
    except Exception as e:
        logger.warning(f"[pinterest] Screenshot failed: {e}")

async def _launch_browser(playwright) -> tuple[Browser, BrowserContext]:
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--no-first-run",
            "--no-zygote",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            # ── memory reducers (Chromium is the main OOM risk on small hosts) ──
            "--disable-features=site-per-process,IsolateOrigins",  # fewer renderer processes
            "--disable-software-rasterizer",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--renderer-process-limit=1",
            # "--single-process",  # biggest memory saver, but less stable — enable if still OOMing
            "--window-size=1366,768",
            "--lang=en-US,en",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
        device_scale_factor=1,
        java_script_enabled=True,
        accept_downloads=True,
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    await context.add_init_script(STEALTH_JS)
    logger.info("[pinterest] Browser launched (headless Chromium)")
    return browser, context

async def save_session(context: BrowserContext):
    try:
        storage = await context.storage_state()
        with open(SESSION_FILE, "w") as f:
            json.dump(storage, f, indent=2)
        logger.info(f"[pinterest] Session saved → {SESSION_FILE}")
    except Exception as e:
        logger.error(f"[pinterest] save_session error: {e}")

async def load_session(playwright) -> tuple[Browser, BrowserContext] | tuple[None, None]:
    if not Path(SESSION_FILE).exists():
        logger.info("[pinterest] No saved session found")
        return None, None
    try:
        browser, _ = await _launch_browser(playwright)
        context = await browser.new_context(
            storage_state=SESSION_FILE,
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
        )
        await context.add_init_script(STEALTH_JS)
        logger.info(f"[pinterest] Session loaded from {SESSION_FILE}")
        return browser, context
    except Exception as e:
        logger.error(f"[pinterest] load_session error: {e}")
        return None, None

async def is_session_valid(context: BrowserContext) -> bool:
    page = await context.new_page()
    try:
        await page.goto("https://www.pinterest.com/", timeout=PINTEREST_TIMEOUT)
        await _human_delay(DELAY_MEDIUM)
        for sel in SELECTORS["logged_in_proof"]:
            try:
                el = await page.wait_for_selector(sel, timeout=5000)
                if el:
                    logger.info("[pinterest] Session is valid ✓")
                    await page.close()
                    return True
            except PlaywrightTimeout:
                continue
        logger.info("[pinterest] Session expired or not logged in")
        await page.close()
        return False
    except Exception as e:
        logger.warning(f"[pinterest] Session check error: {e}")
        await page.close()
        return False

async def login(context: BrowserContext, email: str = "", password: str = "", manual: bool = False) -> bool:
    page = await context.new_page()
    try:
        logger.info("[pinterest] Navigating to login page…")
        await page.goto("https://www.pinterest.com/login/", timeout=PINTEREST_TIMEOUT)
        await _human_delay(DELAY_MEDIUM)

        if not email or not password:
            email    = PINTEREST_EMAIL
            password = PINTEREST_PASS

        if not email or not password:
            logger.error("[pinterest] No credentials provided")
            await page.close()
            return False

        email_el = await _find_first(page, SELECTORS["email_input"])
        if not email_el:
            await page.close()
            return False

        await _human_type_el(email_el, email)
        await _human_delay(DELAY_SHORT)

        pass_el = await _find_first(page, SELECTORS["password_input"])
        if not pass_el:
            await page.close()
            return False

        await _human_type_el(pass_el, password)
        await _human_delay(DELAY_SHORT)

        clicked = await _click_first_matching(page, SELECTORS["login_button"])
        if not clicked:
            await page.close()
            return False

        await _human_delay(DELAY_LONG)
        await page.wait_for_load_state("networkidle", timeout=PINTEREST_TIMEOUT)

        for sel in SELECTORS["logged_in_proof"]:
            try:
                el = await page.wait_for_selector(sel, timeout=8000)
                if el:
                    logger.info("[pinterest] ✓ Logged in successfully")
                    await save_session(context)
                    await page.close()
                    return True
            except PlaywrightTimeout:
                continue

        await page.close()
        return False
    except Exception as e:
        logger.error(f"[pinterest] login() error: {e}")
        await page.close()
        return False

async def _download_image(url: str) -> str | None:
    import aiohttp
    os.makedirs("tmp_pins", exist_ok=True)
    ext  = url.split(".")[-1].split("?")[0]
    ext  = ext if ext in ("jpg", "jpeg", "png", "gif", "webp") else "jpg"
    path = os.path.join("tmp_pins", f"pin_{int(time.time())}_{random.randint(1000,9999)}.{ext}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(path, "wb") as f:
                        f.write(content)
                    return path
    except Exception:
        pass
    return None

async def create_pin(context: BrowserContext, pin_data: dict) -> bool:
    page = await context.new_page()
    image_path = None
    try:
        image_url  = pin_data.get("image_url", "")
        if not image_url:
            await page.close()
            return False
        image_path = await _download_image(image_url)
        if not image_path:
            await page.close()
            return False

        await page.goto("https://www.pinterest.com/pin-creation-tool/", timeout=PINTEREST_TIMEOUT)
        await _human_delay(DELAY_LONG)
        
        upload_el = await _find_first(page, SELECTORS["image_upload"])
        if not upload_el:
            await page.close()
            return False
        await upload_el.set_input_files(image_path)
        await _human_delay(DELAY_LONG)

        title = pin_data.get("title", "")[:100]
        if title:
            title_el = await _find_first(page, SELECTORS["title_input"])
            if title_el: await _human_type_el(title_el, title)

        desc = pin_data.get("description", "")[:500]
        if desc:
            desc_el = await _find_first(page, SELECTORS["description_input"])
            if desc_el: await _human_type_el(desc_el, desc)

        link = pin_data.get("affiliate_link", "")
        if link:
            link_el = await _find_first(page, SELECTORS["link_input"])
            if link_el: await _human_type_el(link_el, link)

        board_name = pin_data.get("board_name", "")
        if board_name:
            await _select_or_create_board(page, board_name)

        await _human_delay(DELAY_MEDIUM)
        published = await _click_publish(page)
        if not published:
            await page.close()
            return False

        success = await _verify_published(page)
        await page.close()
        return success
    except Exception as e:
        logger.error(f"create_pin error: {e}")
        await page.close()
        return False
    finally:
        if image_path and os.path.exists(image_path):
            try: os.remove(image_path)
            except Exception: pass

async def _select_or_create_board(page: Page, board_name: str) -> bool:
    # 1. Ouvrir le menu déroulant des tableaux
    clicked = await _click_first_matching(page, SELECTORS["board_dropdown"], timeout=10000)
    if not clicked: 
        logger.error("[pinterest] Impossible d'ouvrir le menu des tableaux")
        return False
    await _human_delay(DELAY_MEDIUM)
    
    # 2. Chercher si le tableau existe déjà
    search_el = await _find_first(page, SELECTORS["board_search"], timeout=8000)
    if search_el:
        await _human_type_el(search_el, board_name)
        await _human_delay(DELAY_MEDIUM)
        
    # Essayer de cliquer sur le tableau s'il apparaît dans la liste filtrée
    board_found = False
    for sel in SELECTORS["board_option"]:
        try:
            options = await page.query_selector_all(sel)
            for opt in options:
                text = await opt.inner_text()
                if board_name.lower() in text.lower():
                    await opt.click()
                    logger.info(f"[pinterest] Tableau existant sélectionné : {board_name}")
                    board_found = True
                    return True
        except Exception: 
            continue

    # 3. Si le tableau n'existe pas, on le crée !
    if not board_found:
        logger.info(f"[pinterest] Tableau '{board_name}' introuvable. Création automatique...")
        
        # Cliquer sur "Créer un tableau"
        created_btn_clicked = await _click_first_matching(page, SELECTORS["create_board_btn"], timeout=8000)
        if not created_btn_clicked:
            logger.error("[pinterest] Impossible de trouver le bouton 'Créer un tableau'")
            return False
        await _human_delay(DELAY_MEDIUM)
        
        # Taper le nom du nouveau tableau
        name_input = await _find_first(page, SELECTORS["new_board_name_input"], timeout=8000)
        if name_input:
            await _human_type_el(name_input, board_name)
            await _human_delay(DELAY_SHORT)
            
            # Valider la création du tableau
            await _click_first_matching(page, SELECTORS["confirm_create_board"], timeout=8000)
            logger.info(f"[pinterest] Nouveau tableau '{board_name}' créé avec succès !")
            await _human_delay(DELAY_LONG)
            return True
            
    return False

async def _click_publish(page: Page) -> bool:
    for sel in SELECTORS["publish_button"]:
        try:
            btn = await page.wait_for_selector(sel, timeout=10000)
            if btn:
                # Si le bouton est encore désactivé (grisé), on attend un peu que l'image finisse de charger
                if not await btn.is_enabled():
                    logger.info("[pinterest] Le bouton Publier est grisé, attente du chargement complet...")
                    await asyncio.sleep(5)
                await btn.scroll_into_view_if_needed()
                await _human_delay(DELAY_SHORT)
                await btn.click()
                return True
        except Exception: continue
    return False

async def _verify_published(page: Page) -> bool:
    await _human_delay(DELAY_MEDIUM)
    for sel in SELECTORS["pin_saved_toast"]:
        try:
            el = await page.wait_for_selector(sel, timeout=10000)
            if el: return True
        except Exception: continue
    return "pin-creation-tool" not in page.url

def update_pinterest_status(asin: str, status: str = "published"):
    if not sheets_handler: return
    try:
        sheet = sheets_handler.get_sheet()
        asin_col = sheet.col_values(11)
        for idx, val in enumerate(asin_col):
            if val == asin:
                sheet.update_cell(idx + 1, 14, status)
                return
    except Exception as e:
        logger.error(f"update_status error: {e}")

async def publish_pin(pin_data: dict) -> bool:
    async with async_playwright() as pw:
        browser, context = await load_session(pw)
        if context is None:
            browser, context = await _launch_browser(pw)
            success = await login(context)
            if not success:
                await browser.close()
                return False

        valid = await is_session_valid(context)
        if not valid:
            await browser.close()
            browser, context = await _launch_browser(pw)
            success = await login(context)
            if not success:
                await browser.close()
                return False

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            success = await create_pin(context, pin_data)
            if success: break
            if attempt < MAX_RETRIES:
                await asyncio.sleep(30 * attempt)

        asin = pin_data.get("asin", "")
        if asin:
            update_pinterest_status(asin, "published" if success else "failed")
        await browser.close()
        return success

async def publish_pending_pins(max_pins: int = 10):
    if not sheets_handler: return
    try:
        sheet   = sheets_handler.get_sheet()
        records = sheet.get_all_records()
    except Exception as e:
        logger.error(f"Sheets read error: {e}")
        return

    pending = [
        r for r in records
        if str(r.get("pinterest_status", "")).lower() in ("pending", "")
        and r.get("Media URL", "")
    ]
    if not pending: return

    published = 0
    for row in pending[:max_pins]:
        pin_data = {
            "title":          row.get("Title", "")[:100],
            "description":    row.get("Description", "")[:500],
            "affiliate_link": row.get("Link", "") or row.get("Affiliate Link", ""),
            "image_url":      row.get("Media URL", ""),
            "board_name":     row.get("Pinterest Board", "Amazon Deals"),
            "asin":           row.get("ASIN", ""),
        }
        success = await publish_pin(pin_data)
        if success: published += 1
        if published < len(pending[:max_pins]):
            await asyncio.sleep(random.uniform(180, 480))

def publish_pending_sync(max_pins: int = 5):
    try: asyncio.run(publish_pending_pins(max_pins=max_pins))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(publish_pending_pins(max_pins=max_pins))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("Execution automatique des pins...")
    asyncio.run(publish_pending_pins(max_pins=3))

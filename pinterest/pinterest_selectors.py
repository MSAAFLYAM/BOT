"""
pinterest/pinterest_selectors.py
Centralized Pinterest DOM selectors.

Pinterest changes selectors frequently. Keeping them here means
updating ONE file when the UI changes — not hunting through code.

Each field has MULTIPLE fallback selectors tried in order.
"""
from __future__ import annotations

# ── Login detection ────────────────────────────────────────────────────────────
LOGIN_URL    = "https://www.pinterest.com/login/"
SETTINGS_URL = "https://www.pinterest.com/settings/"
HOME_URL     = "https://www.pinterest.com/"
CREATE_URL   = "https://www.pinterest.com/pin-creation-tool/"

# ── Login form ───────────────────────────────────────────────────────────────
LOGIN_EMAIL    = ['input[type="email"]', 'input[name="id"]', '#email']
LOGIN_PASSWORD = ['input[type="password"]', 'input[name="password"]', '#password']
LOGIN_SUBMIT   = ['button[type="submit"]', 'button:has-text("Log in")', 'div[data-test-id="registerFormSubmitButton"] button']

# ── Image upload ───────────────────────────────────────────────────────────────
FILE_INPUT     = ['input[type="file"]']
UPLOAD_BUTTONS = [
    '[data-test-id="storyboard-upload-btn"]',
    '[data-test-id="media-upload-button"]',
    '[data-test-id="media-upload-input"]',
    'button:has-text("upload")',
    'div[role="button"]:has-text("drag")',
]

# ── Pin fields ───────────────────────────────────────────────────────────────
TITLE_FIELDS = [
    'textarea#storyboard-selector-title',
    '[data-test-id="pin-draft-title"] textarea',
    'textarea[placeholder*="title" i]',
    'textarea[aria-label*="title" i]',
    '#pin-draft-title',
]
DESC_FIELDS = [
    '[data-test-id="pin-draft-description"] div[contenteditable="true"]',
    '[data-test-id="pin-draft-description"] textarea',
    'div[contenteditable="true"][aria-label*="description" i]',
    'div[contenteditable="true"][data-test-id*="description" i]',
    'div[contenteditable="true"]',
]
LINK_FIELDS = [
    'textarea#WebsiteField',
    '[data-test-id="pin-draft-link"] textarea',
    'textarea[placeholder*="link" i]',
    'textarea[aria-label*="link" i]',
    '#pin-draft-link',
]

# ── Board selection ─────────────────────────────────────────────────────────
BOARD_DROPDOWN = [
    '[data-test-id="board-dropdown-select-button"]',
    '[data-test-id="boardDropdownSelectButton"]',
    'button[aria-label*="board" i]',
]
BOARD_SEARCH = [
    'input[placeholder*="Search" i]',
    'input[aria-label*="Search" i]',
]
BOARD_OPTION  = 'div[title="{name}"]'      # .format(name=board_name)
BOARD_CREATE  = [
    'button:has-text("Create board")',
    'div[role="button"]:has-text("Create")',
    'button:has-text("Create")',
]

# ── Publish ─────────────────────────────────────────────────────────────────
PUBLISH_BUTTONS = [
    '[data-test-id="board-dropdown-save-button"]',
    '[data-test-id="storyboard-creation-nav-done"]',
    'button:has-text("Publish")',
    'button:has-text("Save")',
]

# ── Logged-in markers ───────────────────────────────────────────────────────
LOGGED_IN_MARKERS = [
    '[data-test-id="header-profile"]',
    '[data-test-id="header-menu-profile"]',
    'div[data-test-id="boardsTab"]',
]

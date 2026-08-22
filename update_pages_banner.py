#!/usr/bin/env python3
"""
update_pages_banner.py
Adds a beautiful banner header to each of the 7 existing Blogger Pages.
Does NOT delete or recreate pages — UPDATES them by prepending the banner HTML.
"""

import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN")
BLOG_ID = os.getenv("BLOGGER_BLOG_ID") or os.getenv("BLOG_ID")

TIMEOUT = 30
RATE_LIMIT_DELAY = 3
MAX_RETRIES = 4
BACKOFF_BASE = 60

EXPECTED_TITLES = [
    "About Us",
    "How It Works",
    "Contact Us",
    "Affiliate Disclosure",
    "Privacy Policy",
    "Disclaimer",
    "Terms of Use",
]


def get_access_token():
    """Exchange refresh token for an access token."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        print("[ERROR] No access token returned.")
        sys.exit(1)
    return token


def api_request(method, url, headers, json_body=None, params=None):
    """Make an API request with retry on 429."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=TIMEOUT,
            )
            if resp.status_code == 429:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"    [429] Rate limited. Waiting {wait}s (attempt {attempt+1}/{MAX_RETRIES})...")
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.RequestException as e:
            print(f"    [ERROR] Request failed: {e}")
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"    Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
    print("    [ERROR] Max retries exceeded.")
    return None


def list_pages(token):
    """List all pages for the blog."""
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/pages"
    headers = {"Authorization": f"Bearer {token}"}
    resp = api_request("GET", url, headers)
    if resp is None or resp.status_code != 200:
        print(f"[ERROR] Failed to list pages: {resp}")
        return []
    data = resp.json()
    return data.get("items", [])


def get_page(token, page_id):
    """Fetch a single page by ID."""
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/pages/{page_id}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = api_request("GET", url, headers)
    if resp is None or resp.status_code != 200:
        print(f"    [ERROR] Failed to fetch page {page_id}: {resp}")
        return None
    return resp.json()


def update_page(token, page_id, page_data):
    """Update a page via PUT."""
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = api_request("PUT", url, headers, json_body=page_data)
    if resp is None or resp.status_code not in (200, 201):
        print(f"    [ERROR] Failed to update page {page_id}: {resp}")
        return None
    return resp.json()


def build_banner(title):
    """Build the banner HTML with the given page title."""
    # Escape title for safe HTML
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<style>
.nd-page-banner{{width:100%;background:#0B1E3D;background-image:repeating-linear-gradient(135deg,transparent,transparent 40px,rgba(255,255,255,.015) 40px,rgba(255,255,255,.015) 80px);padding:40px 32px 32px;margin:0 0 24px;position:relative;overflow:hidden;border-radius:12px;}}
.nd-page-banner-top{{display:flex;align-items:center;gap:10px;margin-bottom:32px;}}
.nd-page-banner-logo{{font-size:22px;font-weight:900;color:#fff;letter-spacing:-.02em;}}
.nd-page-banner-logo span{{color:#FF9900;}}
.nd-page-banner-tagline{{font-size:12px;color:rgba(255,255,255,.55);margin-top:2px;}}
.nd-page-banner-title{{text-align:center;margin:0 0 16px;}}
.nd-page-banner-title h1{{font-size:clamp(28px,5vw,42px);font-weight:900;color:#fff;margin:0 0 12px;letter-spacing:-.02em;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}}
.nd-page-banner-title .nd-underline{{width:60px;height:4px;background:#FF9900;border-radius:2px;margin:0 auto;}}
.nd-page-banner-bottom{{display:flex;justify-content:flex-end;align-items:center;padding-top:16px;border-top:2px solid #FF9900;margin-top:24px;}}
.nd-page-banner-copy{{font-size:11px;color:rgba(255,255,255,.45);font-weight:500;}}
@media(max-width:767px){{.nd-page-banner{{padding:28px 20px 24px;}}}}
</style>
<div class="nd-page-banner">
  <div class="nd-page-banner-top">
    <div>
      <div class="nd-page-banner-logo">NEST <span>DEALS</span></div>
      <div class="nd-page-banner-tagline">Smart Picks. Real Value.</div>
    </div>
  </div>
  <div class="nd-page-banner-title">
    <h1>{safe_title}</h1>
    <div class="nd-underline"></div>
  </div>
  <div class="nd-page-banner-bottom">
    <span class="nd-page-banner-copy">© 2026 NEST DEALS — nestdeal.blogspot.com</span>
  </div>
</div>"""


def normalize_title(title):
    """Normalize a page title for matching (lowercase, strip)."""
    return title.strip().lower()


def main():
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, BLOG_ID]):
        print("[ERROR] Missing required environment variables.")
        print("  Need: BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, BLOGGER_BLOG_ID (or BLOG_ID)")
        sys.exit(1)

    print("=" * 60)
    print("NEST DEALS — Page Banner Updater")
    print("=" * 60)
    print()

    # Step 1: Get access token
    print("[1/3] Authenticating with Blogger API...")
    try:
        token = get_access_token()
        print("  OK — Access token obtained.")
    except Exception as e:
        print(f"  FAILED — {e}")
        sys.exit(1)

    print()

    # Step 2: List all pages
    print("[2/3] Listing existing pages...")
    pages = list_pages(token)
    print(f"  Found {len(pages)} page(s).")

    if not pages:
        print("[ERROR] No pages found on the blog.")
        sys.exit(1)

    print()
    print("  Existing pages:")
    for p in pages:
        print(f"    - \"{p.get('title', '(untitled)')}\" (ID: {p.get('id')})")

    # Step 3: Build lookup of expected titles → page objects
    title_map = {}
    for page in pages:
        norm = normalize_title(page.get("title", ""))
        title_map[norm] = page

    # Step 4: Update each expected page
    print()
    print("[3/3] Updating pages with banner...")
    print()

    updated = 0
    skipped = 0
    failed = 0
    already_had_banner = 0

    for expected_title in EXPECTED_TITLES:
        norm = normalize_title(expected_title)

        if norm not in title_map:
            print(f"  '{expected_title}' — NOT FOUND (skipping)")
            skipped += 1
            continue

        page = title_map[norm]
        page_id = page.get("id")

        print(f"  Updating '{expected_title}'...", end=" ", flush=True)

        # Fetch full page content
        try:
            full_page = get_page(token, page_id)
        except Exception as e:
            print(f"FAILED (fetch error: {e})")
            failed += 1
            continue

        if full_page is None:
            print("FAILED (could not fetch page)")
            failed += 1
            continue

        current_content = full_page.get("content", "")

        # Check if banner already exists (idempotent guard)
        if "nd-page-banner" in current_content:
            print("ALREADY HAS BANNER (skipping)")
            already_had_banner += 1
            continue

        # Prepend banner to content
        banner_html = build_banner(expected_title)
        new_content = banner_html + "\n" + current_content

        # Build update payload (keep all existing fields, override content)
        update_payload = {
            "kind": "blogger#page",
            "id": page_id,
            "content": new_content,
        }

        # Also pass through title and status to avoid overwriting
        if full_page.get("title"):
            update_payload["title"] = full_page["title"]
        if full_page.get("status"):
            update_payload["status"] = full_page["status"]

        # Update the page
        try:
            result = update_page(token, page_id, update_payload)
        except Exception as e:
            print(f"FAILED (update error: {e})")
            failed += 1
            continue

        if result and result.get("id"):
            print("DONE")
            updated += 1
        else:
            print("FAILED (no ID returned)")
            failed += 1

        # Rate limit between API calls
        time.sleep(RATE_LIMIT_DELAY)

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Updated:      {updated}")
    print(f"  Already had:  {already_had_banner}")
    print(f"  Not found:    {skipped}")
    print(f"  Failed:       {failed}")
    print()

    # Also list any pages that weren't in our expected list
    expected_norms = {normalize_title(t) for t in EXPECTED_TITLES}
    other_pages = [p for p in pages if normalize_title(p.get("title", "")) not in expected_norms]
    if other_pages:
        print("  Other pages (not in expected list):")
        for p in other_pages:
            print(f"    - \"{p.get('title', '(untitled)')}\" (ID: {p.get('id')})")
        print()

    print("Done!")


if __name__ == "__main__":
    main()

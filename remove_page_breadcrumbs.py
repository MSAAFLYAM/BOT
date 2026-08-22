import os
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN")
BLOG_ID = os.getenv("BLOGGER_BLOG_ID") or os.getenv("BLOG_ID")

TARGET_PAGES = {
    "About Us",
    "How It Works",
    "Contact Us",
    "Affiliate Disclosure",
    "Privacy Policy",
    "Disclaimer",
    "Terms of Use",
}

BREADCRUMB_PATTERNS = [
    r'<div class="nd-subtitle"[^>]*><a href=["\']\/["\']>\s*Home\s*<\/a>\s*(?:&gt;|>)\s*[^<]*<\/div>',
    r'<p class="nd-subtitle"[^>]*><a href=["\']\/["\']>\s*Home\s*<\/a>\s*(?:&gt;|>)\s*[^<]*<\/p>',
    r'<div class="nd-subtitle"[^>]*><a href=["\']\/["\']>\s*Home\s*<\/a>\s*(?:&gt;|>)\s*[^<]*<\/div>',
    r'<p class="nd-subtitle"[^>]*><a href=["\']\/["\']>\s*Home\s*<\/a>\s*(?:&gt;|>)\s*[^<]*<\/p>',
    r'<p class="nd-subtitle"[^>]*><a href=\'\/\'>Home<\/a>\s*(?:&gt;|>)\s*[^<]*</p>',
    r'<div class="nd-subtitle"[^>]*><a href=\'\/\'>Home<\/a>\s*(?:&gt;|>)\s*[^<]*</div>',
]

BREADCRUMB_RE = re.compile("|".join(BREADCRUMB_PATTERNS))


def get_token():
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def api_call(method, url, token, json_body=None, retries=3):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(retries + 1):
        resp = requests.request(method, url, headers=headers, json=json_body, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)
            print(f"  429 rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        return resp
    return resp


def main():
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, BLOG_ID]):
        print("ERROR: Missing env vars. Check BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, BLOG_ID")
        return

    print("Getting OAuth2 token...")
    token = get_token()

    print("Listing pages...")
    resp = api_call("GET", f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/pages/", token)
    resp.raise_for_status()
    pages = resp.json().get("items", [])
    print(f"Found {len(pages)} pages total.")

    matched = [p for p in pages if p["title"] in TARGET_PAGES]
    print(f"Matched {len(matched)} target pages.\n")

    removed_count = 0
    skipped_count = 0

    for page in matched:
        pid = page["id"]
        title = page["title"]
        print(f"Processing: {title} (id={pid})")
        time.sleep(3)

        resp = api_call("GET", f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/pages/{pid}", token)
        resp.raise_for_status()
        full = resp.json()
        body = full.get("content", full.get("body", ""))

        new_body, n = BREADCRUMB_RE.subn("", body)
        new_body = re.sub(r"\n{3,}", "\n\n", new_body).strip()

        if n == 0:
            print(f"  No breadcrumb found. Skipping.")
            skipped_count += 1
            continue

        time.sleep(3)
        resp = api_call(
            "PUT",
            f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/pages/{pid}",
            token,
            json_body={"content": new_body},
        )
        resp.raise_for_status()
        print(f"  Removed breadcrumb ({n} occurrence(s)). Updated.")
        removed_count += 1

    print(f"\n{'='*40}")
    print(f"Done. Breadcrumbs removed: {removed_count}, Skipped: {skipped_count}")


if __name__ == "__main__":
    main()

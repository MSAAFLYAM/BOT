# update_all_articles.py — Update ALL published articles with premium template
import os
import sys
import re
import json
import time
import logging
import requests
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Load env
from dotenv import load_dotenv
load_dotenv()

# Import publisher
import blogger_api_publisher as pub

def get_access_token():
    return pub._get_access_token()

def list_all_posts():
    """Get all published posts from Blogger."""
    token = get_access_token()
    posts = []
    page_token = None
    
    while True:
        params = {"maxResults": 50, "status": "live"}
        if page_token:
            params["pageToken"] = page_token
        
        resp = requests.get(
            f"{pub.BLOGGER_BASE}/blogs/{pub.BLOG_ID}/posts",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        
        if resp.status_code != 200:
            logger.error(f"Failed to list posts: {resp.status_code}")
            break
        
        data = resp.json()
        items = data.get("items", [])
        posts.extend(items)
        
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        
        time.sleep(0.5)
    
    return posts

def extract_product_from_post(post):
    """Extract product data from existing Blogger post HTML."""
    content = post.get("content", "")
    title = post.get("title", "")
    
    product = {
        "title": title,
        "price": "",
        "original_price": "",
        "img_url": "",
        "rating": 0,
        "review_count": 0,
        "aff_link": "",
        "asin": "",
        "features": [],
    }
    
    # Extract image URL
    img_match = re.search(r'<img[^>]+src="([^"]+)"', content)
    if img_match:
        product["img_url"] = img_match.group(1)
    
    # Extract price from content
    price_match = re.search(r'\$(\d+\.?\d*)', content)
    if price_match:
        product["price"] = f"${price_match.group(1)}"
    
    # Extract affiliate link
    aff_match = re.search(r'href="(https?://www\.amazon\.com/[^"]*tag=[^"]*)"', content)
    if aff_match:
        product["aff_link"] = aff_match.group(1)
    else:
        aff_match = re.search(r'href="(https?://www\.amazon\.com/[^"]*)"', content)
        if aff_match:
            product["aff_link"] = aff_match.group(1)
    
    # Extract ASIN
    asin_match = re.search(r'/dp/([A-Z0-9]{10})', content)
    if asin_match:
        product["asin"] = asin_match.group(1)
    
    # Extract features from list items
    feature_matches = re.findall(r'<li[^>]*>(.*?)</li>', content, re.DOTALL)
    if feature_matches:
        features = []
        for f in feature_matches[:6]:
            clean = re.sub(r'<[^>]+>', '', f).strip()
            if clean and len(clean) > 10 and len(clean) < 200:
                features.append(clean)
        product["features"] = features
    
    return product

def update_post(post_id, title, html_content, labels):
    """Update a Blogger post with new content."""
    token = get_access_token()
    
    payload = {
        "title": title,
        "content": html_content,
        "labels": labels or [],
    }
    
    resp = requests.put(
        f"{pub.BLOGGER_BASE}/blogs/{pub.BLOG_ID}/posts/{post_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    
    if resp.status_code == 200:
        return True, resp.json().get("url", "")
    else:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"

def main():
    print("=" * 60)
    print("PREMIUM TEMPLATE UPDATER")
    print("=" * 60)
    
    if not pub.is_configured():
        print("❌ Blogger API not configured!")
        return
    
    print("\n📥 Fetching all published posts...")
    posts = list_all_posts()
    print(f"   Found {len(posts)} posts\n")
    
    success = 0
    failed = 0
    skipped = 0
    
    for i, post in enumerate(posts, 1):
        post_id = post.get("id", "")
        old_title = post.get("title", "")
        labels = post.get("labels", [])
        
        print(f"[{i}/{len(posts)}] {old_title[:50]}...")
        
        try:
            # Extract product data
            product = extract_product_from_post(post)
            
            # Generate AI content
            description = f"Product: {product['title']}"
            if product['price']:
                description += f" priced at {product['price']}"
            
            # Build new article
            new_title, new_html = pub._build_article(product, description)
            
            # Validate
            issues = pub._validate_article(new_html, new_title)
            if issues:
                print(f"   ⚠️ Validation issues: {issues}")
            
            # Update post
            ok, result = update_post(post_id, new_title, new_html, labels)
            
            if ok:
                success += 1
                print(f"   ✅ Updated: {result[:60]}...")
            else:
                failed += 1
                print(f"   ❌ Failed: {result}")
            
            # Rate limit
            time.sleep(2)
            
        except Exception as e:
            failed += 1
            print(f"   ❌ Error: {str(e)[:80]}")
    
    print("\n" + "=" * 60)
    print(f"COMPLETE: {success} success, {failed} failed, {skipped} skipped")
    print("=" * 60)

if __name__ == "__main__":
    main()

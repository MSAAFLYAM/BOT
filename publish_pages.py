# -*- coding: utf-8 -*-
"""
publish_pages.py - Publish 7 static Blogger PAGES (not posts) via Blogger API v3.

Usage:
    C:\Python314\python.exe publish_pages.py
"""

import os
import sys
import time
import json
import logging
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET", "")
REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN", "")
BLOG_ID = os.getenv("BLOGGER_BLOG_ID") or os.getenv("BLOG_ID", "")

BASE_URL = "https://www.googleapis.com/blogger/v3"
SITE_NAME = "NestDeal"
BLOG_URL = "https://nestdeal.blogspot.com/"
CONTACT_EMAIL = "nestdeal@gmail.com"

PAGE_STATUS = "live"
RATE_LIMIT_DELAY = 3
MAX_RETRIES = 5

def _get_access_token():
    data = urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    req = Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req) as resp:
        body = json.loads(resp.read())
    return body["access_token"]


def _api_request(method, path, body=None, access_token=None):
    url = BASE_URL + path
    if "?" in url:
        url += "&alt=json"
    else:
        url += "?alt=json"
    data = json.dumps(body).encode() if body else None
    for attempt in range(1, MAX_RETRIES + 1):
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if access_token:
            req.add_header("Authorization", "Bearer " + access_token)
        try:
            with urlopen(req) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            if exc.code == 429:
                wait = RATE_LIMIT_DELAY * (2 ** (attempt - 1))
                log.warning("Rate-limited (429). Retrying in %ss ...", wait)
                time.sleep(wait)
                continue
            body_text = exc.read().decode(errors="replace")
            log.error("API error %s on %s: %s", exc.code, path, body_text[:500])
            raise
    raise RuntimeError("Failed after %d retries for %s" % (MAX_RETRIES, path))


def list_pages(access_token):
    pages = []
    page_token = None
    while True:
        path = "/blogs/%s/pages?maxResults=50&status=%s" % (BLOG_ID, PAGE_STATUS)
        if page_token:
            path += "&pageToken=" + page_token
        result = _api_request("GET", path, access_token=access_token)
        pages.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return pages


def get_page_by_title(title, pages):
    for p in pages:
        if p.get("title", "").strip().lower() == title.strip().lower():
            return p
    return None


def delete_page(page_id, access_token):
    _api_request("DELETE", "/blogs/%s/pages/%s" % (BLOG_ID, page_id), access_token=access_token)
    log.info("  Deleted page %s", page_id)


def create_page(title, content, access_token):
    body = {"title": title, "content": content, "status": PAGE_STATUS}
    return _api_request("POST", "/blogs/%s/pages" % BLOG_ID, body=body, access_token=access_token)


def update_page(page_id, title, content, access_token):
    body = {"title": title, "content": content, "status": PAGE_STATUS}
    return _api_request("PUT", "/blogs/%s/pages/%s" % (BLOG_ID, page_id), body=body, access_token=access_token)

def _page_shell(title, body_html):
    css = (
        "*{margin:0;padding:0;box-sizing:border-box}"
        "body{font-family:'Inter',sans-serif;background:#F7F8FA;color:#172033;line-height:1.7;padding:0}"
        ".nd-container{max-width:760px;margin:0 auto;padding:40px 24px}"
        "h1{font-size:2rem;font-weight:700;margin-bottom:8px;color:#172033}"
        ".nd-subtitle{font-size:1.1rem;color:#555;margin-bottom:32px}"
        "h2{font-size:1.35rem;font-weight:600;margin:32px 0 12px;color:#172033}"
        "h3{font-size:1.1rem;font-weight:600;margin:20px 0 8px;color:#172033}"
        "p{margin-bottom:16px;color:#333}"
        "a{color:#FF9900;text-decoration:none;font-weight:500}"
        "a:hover{text-decoration:underline}"
        "ul,ol{margin:0 0 20px 24px}"
        "li{margin-bottom:8px;color:#333}"
        ".nd-card{background:#fff;border:1px solid #E4E7EC;border-radius:12px;padding:24px;margin-bottom:20px}"
        ".nd-badge{display:inline-block;background:#FF9900;color:#fff;font-size:.75rem;font-weight:600;padding:4px 12px;border-radius:20px;margin-bottom:12px}"
        ".nd-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}"
        "@media(max-width:600px){.nd-grid{grid-template-columns:1fr}}"
        ".nd-step{background:#fff;border:1px solid #E4E7EC;border-radius:12px;padding:20px;text-align:center}"
        ".nd-step-num{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:50%;background:#FF9900;color:#fff;font-weight:700;font-size:1rem;margin-bottom:8px}"
        ".nd-step h3{margin:0 0 6px}"
        ".nd-highlight{background:#FFF7E6;border-left:4px solid #FF9900;padding:16px 20px;border-radius:0 8px 8px 0;margin-bottom:20px}"
        ".nd-footer{margin-top:48px;padding-top:24px;border-top:1px solid #E4E7EC;font-size:.85rem;color:#888;text-align:center}"
        "table{width:100%;border-collapse:collapse;margin-bottom:20px}"
        "th,td{text-align:left;padding:10px 14px;border-bottom:1px solid #E4E7EC;font-size:.95rem}"
        "th{font-weight:600;background:#F7F8FA}"
    )
    return (
        "<!DOCTYPE html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>" + title + " \u2013 " + SITE_NAME + "</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
        "<style>" + css + "</style>"
        "</head>"
        "<body>"
        '<div class="nd-container">'
        + body_html +
        '<div class="nd-footer">&copy; 2024\u20132026 ' + SITE_NAME + '. All rights reserved.</div>'
        "</div>"
        "</body>"
        "</html>"
    )


def _bc(items):
    links = ["<a href='/'>Home</a>"]
    for item in items:
        links.append(item)
    return '<p class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px">' + ' &gt; '.join(links) + '</p>'

def page_about_us():
    s = SITE_NAME
    body = _bc(["About Us"]) + """
<div class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px"><a href="/">Home</a> &gt; About Us</div>
<h1>About %(s)s</h1>
<p class="nd-subtitle">Helping you discover the best products &mdash; without the research headache.</p>
<div class="nd-card">
<h2>Our Mission</h2>
<p>At <strong>%(s)s</strong>, we believe buying online should be simple, transparent, and stress-free. Every day millions of products flood Amazon, making it hard to know which ones are actually worth your money. That is where we come in.</p>
<p>We research, compare, and review products so you do not have to spend hours reading through thousands of reviews. Our mission is to surface the <em>genuinely best-value products</em> across popular categories &mdash; backed by real data, not hype.</p>
</div>
<div class="nd-card">
<h2>What We Do</h2>
<ul>
<li><strong>Research</strong> &mdash; We analyze Amazon listings, customer reviews, ratings, and pricing history.</li>
<li><strong>Review</strong> &mdash; Our editorial process breaks down specs, pros, cons, and real-world performance.</li>
<li><strong>Recommend</strong> &mdash; We pick the top products in each category so you can buy with confidence.</li>
<li><strong>Save</strong> &mdash; We highlight deals, discounts, and best-value picks to help you spend wisely.</li>
</ul>
</div>
<div class="nd-card">
<h2>Why Trust %(s)s?</h2>
<p>We are not here to push the most expensive option. We are here to find the <em>right</em> option. Our recommendations are based on:</p>
<ul>
<li>Verified Amazon ratings and review analysis</li>
<li>Price-to-value comparisons</li>
<li>Product quality and durability indicators</li>
<li>Real customer feedback and satisfaction scores</li>
</ul>
</div>
<h2>Our Categories</h2>
<div class="nd-grid">
<div class="nd-card"><div class="nd-badge">Home &amp; Kitchen</div><p>From cookware to smart home gadgets, we find the essentials that upgrade your everyday living.</p></div>
<div class="nd-card"><div class="nd-badge">Beauty &amp; Health</div><p>Skincare, wellness, and personal care products that deliver real results without the premium price tag.</p></div>
<div class="nd-card"><div class="nd-badge">Sports &amp; Outdoors</div><p>Whether you train daily or explore on weekends, we recommend gear that performs.</p></div>
<div class="nd-card"><div class="nd-badge">Tech &amp; Gadgets</div><p>Phones, accessories, smart devices &mdash; the tech worth your attention, not just the latest release.</p></div>
</div>"""
    return _page_shell("About Us", body % {"s": s})

def page_how_it_works():
    s = SITE_NAME
    body = _bc(["How It Works"]) + """
<div class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px"><a href="/">Home</a> &gt; How It Works</div>
<h1>How %(s)s Works</h1>
<p class="nd-subtitle">Four simple steps from product discovery to your best purchase.</p>
<div class="nd-grid">
<div class="nd-step"><div class="nd-step-num">1</div><h3>Search</h3><p>We monitor Amazon across multiple categories, tracking thousands of products and new arrivals every day.</p></div>
<div class="nd-step"><div class="nd-step-num">2</div><h3>Review</h3><p>Our editorial process analyzes ratings, reviews, pricing trends, and specifications to separate quality from noise.</p></div>
<div class="nd-step"><div class="nd-step-num">3</div><h3>Recommend</h3><p>We publish detailed, honest product reviews highlighting the pros, cons, and who each product is best suited for.</p></div>
<div class="nd-step"><div class="nd-step-num">4</div><h3>Save</h3><p>You get a curated pick with a direct link to Amazon. No more decision fatigue &mdash; just smart shopping.</p></div>
</div>
<div class="nd-card">
<h2>Our Review Process</h2>
<p>Every product published on %(s)s goes through a structured evaluation:</p>
<ol>
<li><strong>Data Collection</strong> &mdash; We pull product data including price, rating, review count, and feature sets.</li>
<li><strong>Value Scoring</strong> &mdash; Our scoring model compares price-to-quality ratio against similar products.</li>
<li><strong>Content Generation</strong> &mdash; AI-assisted analysis produces pros, cons, specifications, and verdict summaries.</li>
<li><strong>Human Oversight</strong> &mdash; Content is reviewed for accuracy, fairness, and usefulness before publishing.</li>
</ol>
</div>
<div class="nd-highlight">
<h2>About Affiliate Links</h2>
<p>%(s)s is a free resource. When you click our links and make a purchase on Amazon, we earn a small commission at <strong>no extra cost to you</strong>. This helps us maintain the site and keep our content free.</p>
<p>We only recommend products we believe offer genuine value. Our affiliate relationship with Amazon does not influence our editorial opinions or product rankings.</p>
</div>
<div class="nd-card">
<h2>What Makes Us Different</h2>
<ul>
<li><strong>No sponsored posts</strong> &mdash; We do not accept paid placements. Our picks are based purely on value analysis.</li>
<li><strong>Transparent methodology</strong> &mdash; We explain how we evaluate products so you can judge for yourself.</li>
<li><strong>Focus on value</strong> &mdash; We prioritize the best price-to-quality ratio, not just the most expensive or cheapest option.</li>
</ul>
</div>"""
    return _page_shell("How It Works", body % {"s": s})

def page_contact_us():
    s = SITE_NAME
    e = CONTACT_EMAIL
    body = _bc(["Contact Us"]) + """
<div class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px"><a href="/">Home</a> &gt; Contact Us</div>
<h1>Contact Us</h1>
<p class="nd-subtitle">Have a question, suggestion, or just want to say hello? We would love to hear from you.</p>
<div class="nd-card">
<h2>Get in Touch</h2>
<p>For general inquiries, feedback, or partnership opportunities, reach us at:</p>
<p><strong>Email:</strong> <a href="mailto:%(e)s">%(e)s</a></p>
<p>We aim to respond to all inquiries within <strong>24-48 hours</strong> during business days.</p>
</div>
<div class="nd-card">
<h2>What You Can Contact Us About</h2>
<ul>
<li><strong>Product Suggestions</strong> &mdash; Want us to review a specific product? Let us know.</li>
<li><strong>Corrections</strong> &mdash; Found an error in one of our reviews? We appreciate corrections.</li>
<li><strong>Partnerships</strong> &mdash; Interested in collaborating? Drop us a line.</li>
<li><strong>General Feedback</strong> &mdash; Tell us what you like, what can improve, or just say hi.</li>
</ul>
</div>
<div class="nd-card">
<h2>Response Times</h2>
<table>
<tr><th>Type</th><th>Expected Response</th></tr>
<tr><td>General Inquiry</td><td>24-48 hours</td></tr>
<tr><td>Product Correction</td><td>12-24 hours</td></tr>
<tr><td>Partnership Request</td><td>3-5 business days</td></tr>
<tr><td>Technical Issue</td><td>24-48 hours</td></tr>
</table>
</div>
<div class="nd-highlight">
<p><strong>Note:</strong> %(s)s is an independent review site. We are not directly affiliated with Amazon or any product manufacturers. For issues with orders, shipping, or returns, please contact the retailer directly.</p>
</div>"""
    return _page_shell("Contact Us", body % {"s": s, "e": e})

def page_affiliate_disclosure():
    s = SITE_NAME
    body = _bc(["Affiliate Disclosure"]) + """
<div class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px"><a href="/">Home</a> &gt; Affiliate Disclosure</div>
<h1>Affiliate Disclosure</h1>
<p class="nd-subtitle">How we make money and stay transparent about it.</p>
<div class="nd-highlight">
<h2>FTC Compliance</h2>
<p>In accordance with the Federal Trade Commission (FTC) guidelines, this disclosure describes the relationship between %(s)s and the products and services we review and recommend.</p>
</div>
<div class="nd-card">
<h2>Amazon Affiliate Program</h2>
<p>%(s)s is a participant in the <strong>Amazon Services LLC Associates Program</strong>, an affiliate advertising program designed to provide a means for sites to earn advertising fees by advertising and linking to Amazon.com.</p>
<p>When you click on product links on our site and make a purchase on Amazon, we may receive a small commission at <strong>no additional cost to you</strong>.</p>
</div>
<div class="nd-card">
<h2>How Affiliate Links Work</h2>
<ol>
<li>You click a product link on %(s)s.</li>
<li>You are redirected to the product page on Amazon.</li>
<li>If you make a purchase (anything in your cart), we may earn a commission.</li>
<li>The price you pay is exactly the same as if you had gone to Amazon directly.</li>
</ol>
</div>
<div class="nd-card">
<h2>Our Commitment to You</h2>
<ul>
<li><strong>Honest Reviews</strong> &mdash; We recommend products based on quality and value, not commission rates.</li>
<li><strong>No Sponsored Content</strong> &mdash; We do not accept payment for product placements or positive reviews.</li>
<li><strong>Transparent Rankings</strong> &mdash; Our rankings are determined by our value scoring model, not by affiliate revenue.</li>
<li><strong>Your Trust Matters</strong> &mdash; Our reputation depends on giving you honest, useful recommendations.</li>
</ul>
</div>
<div class="nd-card">
<h2>Other Affiliate Programs</h2>
<p>In addition to Amazon Associates, %(s)s may participate in other affiliate programs. Each article will clearly indicate if affiliate links are used.</p>
</div>
<div class="nd-highlight">
<p><strong>In short:</strong> We earn a small commission when you buy through our links. This helps us keep the site running and the content free. You pay nothing extra.</p>
</div>"""
    return _page_shell("Affiliate Disclosure", body % {"s": s})

def page_privacy_policy():
    s = SITE_NAME
    e = CONTACT_EMAIL
    body = _bc(["Privacy Policy"]) + """
<div class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px"><a href="/">Home</a> &gt; Privacy Policy</div>
<h1>Privacy Policy</h1>
<p class="nd-subtitle">Your privacy matters. Here is how we handle your data.</p>
<p style="font-size:.9rem;color:#888">Last updated: January 2026</p>
<div class="nd-card">
<h2>1. Information We Collect</h2>
<p>%(s)s collects minimal information to improve your experience:</p>
<ul>
<li><strong>Automatically Collected:</strong> IP address, browser type, device type, referring URL, pages visited, and time spent on pages.</li>
<li><strong>Contact Information:</strong> If you email us directly, we receive your email address and message content.</li>
<li><strong>No Account Registration:</strong> We do not require account creation or collect personal identification information.</li>
</ul>
</div>
<div class="nd-card">
<h2>2. Cookies and Tracking</h2>
<p>%(s)s uses cookies and similar technologies:</p>
<ul>
<li><strong>Analytics Cookies:</strong> To understand how visitors use our site (e.g., Google Analytics).</li>
<li><strong>Affiliate Cookies:</strong> When you click an affiliate link, Amazon may set cookies to track purchases for commission purposes.</li>
<li><strong>Advertising Cookies:</strong> If we display ads, third-party ad networks may use cookies for ad targeting.</li>
</ul>
<p>You can control cookies through your browser settings.</p>
</div>
<div class="nd-card">
<h2>3. Third-Party Services</h2>
<p>We use the following third-party services that may collect data:</p>
<ul>
<li><strong>Google Analytics</strong> &mdash; For website traffic analysis.</li>
<li><strong>Amazon Associates</strong> &mdash; For affiliate link tracking.</li>
<li><strong>Blogger (Google)</strong> &mdash; Our publishing platform.</li>
</ul>
</div>
<div class="nd-card">
<h2>4. How We Use Your Information</h2>
<p>We use collected data solely to:</p>
<ul>
<li>Improve site content and user experience</li>
<li>Understand which products and topics are most useful to readers</li>
<li>Monitor site performance and fix issues</li>
<li>Respond to your inquiries</li>
</ul>
</div>
<div class="nd-card">
<h2>5. Data Sharing</h2>
<p>We do <strong>not</strong> sell, trade, or rent your personal information to third parties. We may share anonymized, aggregated data that cannot identify you individually.</p>
</div>
<div class="nd-card">
<h2>6. Your Rights (GDPR)</h2>
<p>If you are in the European Economic Area (EEA), you have the right to:</p>
<ul>
<li><strong>Access</strong> &mdash; Request a copy of the data we hold about you.</li>
<li><strong>Rectification</strong> &mdash; Request correction of inaccurate data.</li>
<li><strong>Erasure</strong> &mdash; Request deletion of your personal data.</li>
<li><strong>Objection</strong> &mdash; Object to processing of your personal data.</li>
</ul>
<p>To exercise these rights, contact us at <a href="mailto:%(e)s">%(e)s</a>.</p>
</div>
<div class="nd-card">
<h2>7. Children's Privacy</h2>
<p>%(s)s is not directed at children under 13. We do not knowingly collect personal information from children. If you believe a child has provided us with personal data, please contact us and we will remove it.</p>
</div>
<div class="nd-card">
<h2>8. Changes to This Policy</h2>
<p>We may update this Privacy Policy from time to time. Changes will be posted on this page with an updated revision date.</p>
</div>
<div class="nd-highlight">
<p><strong>Questions?</strong> Contact us at <a href="mailto:%(e)s">%(e)s</a> for any privacy-related concerns.</p>
</div>"""
    return _page_shell("Privacy Policy", body % {"s": s, "e": e})

def page_disclaimer():
    s = SITE_NAME
    body = _bc(["Disclaimer"]) + """
<div class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px"><a href="/">Home</a> &gt; Disclaimer</div>
<h1>Disclaimer</h1>
<p class="nd-subtitle">Important information about the content on this site.</p>
<div class="nd-card">
<h2>Product Information Accuracy</h2>
<p>%(s)s strives to provide accurate and up-to-date product information. However, we make no representations or warranties about the completeness, reliability, or accuracy of the content on this site. Product details, specifications, and images are obtained from Amazon and other sources and may change without notice.</p>
</div>
<div class="nd-card">
<h2>Pricing Information</h2>
<p>Product prices and availability are accurate as of the date/time indicated and are subject to change. The price and availability information displayed on %(s)s at the time of purchase will apply. We display prices in USD and may perform currency conversions for reference purposes.</p>
<p><strong>Note:</strong> Prices on Amazon can fluctuate multiple times per day. Always verify the current price on Amazon before making a purchase decision.</p>
</div>
<div class="nd-card">
<h2>Affiliate Links</h2>
<p>%(s)s contains affiliate links to products on Amazon and other retailers. When you click these links and make a purchase, we may earn a commission. This commission comes at no additional cost to you and helps support the operation of this site.</p>
<p>Affiliate relationships do not influence our editorial content or product recommendations. We only recommend products we genuinely believe offer value to our readers.</p>
</div>
<div class="nd-card">
<h2>Professional Advice</h2>
<p>The content on %(s)s is for informational purposes only and should not be considered professional advice. Always consult with qualified professionals before making purchasing decisions, especially for health, safety, or financial products.</p>
</div>
<div class="nd-card">
<h2>Limitation of Liability</h2>
<p>%(s)s shall not be held liable for any indirect, incidental, special, or consequential damages arising out of the use of or inability to use this site or the information contained herein. This includes, but is not limited to, damages for loss of profits, data, or other intangible losses.</p>
</div>
<div class="nd-card">
<h2>External Links</h2>
<p>This site may contain links to external websites that are not operated by us. We have no control over the content and practices of these sites and cannot accept responsibility for their privacy policies or content.</p>
</div>"""
    return _page_shell("Disclaimer", body % {"s": s})

def page_terms_of_use():
    s = SITE_NAME
    e = CONTACT_EMAIL
    body = _bc(["Terms of Use"]) + """
<div class="nd-subtitle" style="font-size:.85rem;margin-bottom:16px"><a href="/">Home</a> &gt; Terms of Use</div>
<h1>Terms of Use</h1>
<p class="nd-subtitle">Please read these terms carefully before using this site.</p>
<p style="font-size:.9rem;color:#888">Last updated: January 2026</p>
<div class="nd-card">
<h2>1. Acceptance of Terms</h2>
<p>By accessing and using %(s)s, you agree to be bound by these Terms of Use. If you do not agree to these terms, please do not use this site.</p>
</div>
<div class="nd-card">
<h2>2. Site Usage</h2>
<p>%(s)s provides product reviews, recommendations, and related content for informational purposes. You may:</p>
<ul>
<li>Browse and read all publicly available content</li>
<li>Share articles via social media or direct links</li>
<li>Click affiliate links to purchase recommended products</li>
</ul>
<p>You may not:</p>
<ul>
<li>Scrape, copy, or reproduce content without permission</li>
<li>Use automated tools to access or download content</li>
<li>Attempt to gain unauthorized access to the site infrastructure</li>
</ul>
</div>
<div class="nd-card">
<h2>3. Intellectual Property</h2>
<p>All content on %(s)s, including text, graphics, logos, and design elements, is the property of %(s)s and is protected by copyright laws. Product images are sourced from Amazon and belong to their respective owners.</p>
<p>You may quote or reference our content with proper attribution and a link to the original article on %(s)s.</p>
</div>
<div class="nd-card">
<h2>4. User Conduct</h2>
<p>When interacting with %(s)s (e.g., via email or comments), you agree to:</p>
<ul>
<li>Be respectful and constructive</li>
<li>Not spam, harass, or abuse other users or staff</li>
<li>Not impersonate any person or entity</li>
<li>Not post content that is unlawful, defamatory, or infringing</li>
</ul>
</div>
<div class="nd-card">
<h2>5. Disclaimer of Warranties</h2>
<p>%(s)s is provided on an "as is" and "as available" basis. We make no warranties, expressed or implied, regarding the site's operation or the accuracy of the information provided. We do not warrant that the site will be uninterrupted, error-free, or free of viruses.</p>
</div>
<div class="nd-card">
<h2>6. Limitation of Liability</h2>
<p>In no event shall %(s)s, its operators, or affiliates be liable for any direct, indirect, incidental, special, or consequential damages arising from:</p>
<ul>
<li>Use of or inability to use this site</li>
<li>Purchases made based on product recommendations</li>
<li>Errors or omissions in content</li>
<li>Unauthorized access to or alteration of data</li>
</ul>
</div>
<div class="nd-card">
<h2>7. Changes to Terms</h2>
<p>We reserve the right to modify these Terms of Use at any time. Changes will be effective immediately upon posting. Your continued use of the site constitutes acceptance of the modified terms.</p>
</div>
<div class="nd-highlight">
<p><strong>Questions about these terms?</strong> Contact us at <a href="mailto:%(e)s">%(e)s</a>.</p>
</div>"""
    return _page_shell("Terms of Use", body % {"s": s, "e": e})

PAGE_FACTORIES = [
    ("About Us", page_about_us),
    ("How It Works", page_how_it_works),
    ("Contact Us", page_contact_us),
    ("Affiliate Disclosure", page_affiliate_disclosure),
    ("Privacy Policy", page_privacy_policy),
    ("Disclaimer", page_disclaimer),
    ("Terms of Use", page_terms_of_use),
]


def main():
    # Validate config
    missing = []
    if not CLIENT_ID:
        missing.append("BLOGGER_CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("BLOGGER_CLIENT_SECRET")
    if not REFRESH_TOKEN:
        missing.append("BLOGGER_REFRESH_TOKEN")
    if not BLOG_ID:
        missing.append("BLOGGER_BLOG_ID or BLOG_ID")
    if missing:
        log.error("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    log.info("Authenticating with Blogger API...")
    token = _get_access_token()
    log.info("Authentication successful.")

    log.info("Listing existing pages...")
    existing = list_pages(token)
    log.info("Found %d existing pages.", len(existing))

    created = 0
    skipped = 0
    failed = 0

    for title, factory in PAGE_FACTORIES:
        print()
        print("=" * 60)
        print("  Page: %s" % title)
        print("=" * 60)

        existing_page = get_page_by_title(title, existing)
        if existing_page:
            log.info("  Page already exists (id=%s). Skipping.", existing_page.get("id"))
            skipped += 1
            continue

        try:
            log.info("  Generating content...")
            content = factory()
            log.info("  Content length: %d chars", len(content))
            log.info("  Creating page...")
            time.sleep(RATE_LIMIT_DELAY)
            result = create_page(title, content, token)
            page_id = result.get("id", "unknown")
            log.info("  Created page: %s (id=%s)", title, page_id)
            created += 1
        except Exception as exc:
            log.error("  FAILED to create page %s: %s", title, exc)
            failed += 1

    print()
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print("  Created:  %d" % created)
    print("  Skipped:  %d" % skipped)
    print("  Failed:   %d" % failed)
    print("=" * 60)


if __name__ == "__main__":
    main()
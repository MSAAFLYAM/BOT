import os
import sys
import time
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN")
BLOG_ID = os.getenv("BLOGGER_BLOG_ID") or os.getenv("BLOG_ID")

BANNER_TEMPLATE = """<style>
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
    <h1>{title}</h1>
    <div class="nd-underline"></div>
  </div>
  <div class="nd-page-banner-bottom">
    <span class="nd-page-banner-copy">&copy; 2026 NEST DEALS &mdash; nestdeal.blogspot.com</span>
  </div>
</div>"""

PAGES = {
    "Affiliate Disclosure": """<h2>Affiliate Disclosure for NestDeal</h2>
<p><strong>Last updated:</strong> August 2026</p>

<p>Welcome to NestDeal. This affiliate disclosure page details our relationship with Amazon and other retailers whose products we review and recommend on this website.</p>

<h2>What Is Affiliate Marketing?</h2>
<p>Affiliate marketing is a way for websites to earn commissions by linking to products sold on other websites. When you click on our affiliate links and make a purchase, we may receive a small commission from the retailer at no extra cost to you.</p>

<h2>Our Relationship with Amazon</h2>
<p>NestDeal is a participant in the <strong>Amazon Services LLC Associates Program</strong>, an affiliate advertising program designed to provide a means for sites to earn advertising fees by advertising and linking to Amazon.com.</p>
<p>This means that when you click on links to various products on this site and make a purchase, we may receive a commission from Amazon. This commission comes at no additional cost to you &mdash; the price you pay on Amazon remains exactly the same whether you use our link or not.</p>

<h2>How This Affects You</h2>
<ul>
<li><strong>No extra cost:</strong> You never pay more by using our affiliate links</li>
<li><strong>Same prices:</strong> The product price on Amazon is the same regardless of how you arrive there</li>
<li><strong>Honest reviews:</strong> Our recommendations are based on product quality, customer reviews, and value &mdash; not commission rates</li>
<li><strong>Transparency:</strong> We clearly mark affiliate links when present</li>
</ul>

<h2>Our Editorial Integrity</h2>
<p>We take our recommendations seriously. The fact that we earn commissions does not influence our editorial content. We:</p>
<ul>
<li>Only recommend products with 4+ star ratings and proven quality</li>
<li>Provide honest pros and cons for every product</li>
<li>Regularly update our reviews to reflect price changes and new versions</li>
<li>Never accept payment for positive reviews</li>
</ul>

<h2>Contact Us</h2>
<p>If you have any questions about this affiliate disclosure, please contact us at <strong>nestdeal@gmail.com</strong>.</p>""",

    "Privacy Policy": """<h2>Privacy Policy for NestDeal</h2>
<p><strong>Last updated:</strong> August 2026</p>

<p>NestDeal ("we," "our," or "us") operates the nestdeal.blogspot.com website. This page informs you of our policies regarding the collection, use, and disclosure of personal information when you use our website.</p>

<h2>Information We Collect</h2>
<p>When you visit our website, we may collect certain information automatically, including:</p>
<ul>
<li><strong>Log Data:</strong> IP address, browser type, pages visited, time spent on pages, and other statistics</li>
<li><strong>Cookies:</strong> We use cookies to enhance your browsing experience and analyze website traffic</li>
<li><strong>Device Information:</strong> Device type, operating system, and browser information</li>
</ul>

<h2>How We Use Your Information</h2>
<p>We use the collected information to:</p>
<ul>
<li>Improve our website and user experience</li>
<li>Analyze website traffic and usage patterns</li>
<li>Provide relevant product recommendations</li>
<li>Respond to your inquiries and comments</li>
</ul>

<h2>Third-Party Services</h2>
<p>We use the following third-party services that may collect information:</p>
<ul>
<li><strong>Google Analytics:</strong> For website traffic analysis</li>
<li><strong>Amazon Associates:</strong> For affiliate link tracking</li>
<li><strong>Blogger (Google):</strong> Our website hosting platform</li>
</ul>

<h2>Cookies</h2>
<p>You can instruct your browser to refuse all cookies or to indicate when a cookie is being sent. However, if you do not accept cookies, some portions of our website may not function properly.</p>

<h2>Data Protection Rights (GDPR)</h2>
<p>If you are located in the European Economic Area (EEA), you have the following data protection rights:</p>
<ul>
<li>The right to access, update, or delete your information</li>
<li>The right of rectification</li>
<li>The right to object to processing</li>
<li>The right to data portability</li>
<li>The right to withdraw consent</li>
</ul>

<h2>Children's Privacy</h2>
<p>Our website is not directed to anyone under 13. We do not knowingly collect personal information from children under 13.</p>

<h2>Changes to This Policy</h2>
<p>We may update this Privacy Policy from time to time. We will notify you of any changes by posting the new policy on this page with an updated "Last updated" date.</p>

<h2>Contact Us</h2>
<p>If you have any questions about this Privacy Policy, please contact us at <strong>nestdeal@gmail.com</strong>.</p>""",

    "Disclaimer": """<h2>Disclaimer for NestDeal</h2>
<p><strong>Last updated:</strong> August 2026</p>

<h2>General Information</h2>
<p>The information provided on NestDeal (nestdeal.blogspot.com) is for general informational purposes only. All information on the site is provided in good faith; however, we make no representation or warranty of any kind, express or implied, regarding the accuracy, adequacy, validity, reliability, availability, or completeness of any information on the site.</p>

<h2>Product Information</h2>
<p>We strive to provide accurate product information, including prices, specifications, and availability. However:</p>
<ul>
<li><strong>Prices may change:</strong> Product prices on Amazon are subject to change without notice. The price shown at the time of our review may differ from the current price.</li>
<li><strong>Availability varies:</strong> Products may go in and out of stock on Amazon without prior notice.</li>
<li><strong>Specifications may differ:</strong> We recommend verifying product specifications on the Amazon product page before purchasing.</li>
<li><strong>Images may differ:</strong> Product images on our site are sourced from Amazon and may differ slightly from the actual product.</li>
</ul>

<h2>Affiliate Links</h2>
<p>NestDeal contains affiliate links to Amazon and other retailers. When you click on these links and make a purchase, we may earn a commission at no extra cost to you. This does not influence our reviews or recommendations.</p>

<h2>Professional Advice</h2>
<p>The content on NestDeal is not intended to be a substitute for professional advice. Always consult with a qualified professional before making any purchasing decisions.</p>

<h2>External Links</h2>
<p>The site may contain links to external websites that are not provided or maintained by or in any way affiliated with NestDeal. Please note that we do not guarantee the accuracy, relevance, timeliness, or completeness of any information on these external websites.</p>

<h2>Testimonials</h2>
<p>The site may contain testimonials by users of our products and/or services. These testimonials reflect the real-life experiences and opinions of such users. However, the experiences are personal to those particular users, and may not necessarily be representative of all users of our products and/or services.</p>

<h2>Limitation of Liability</h2>
<p>In no event shall NestDeal be liable for any indirect, incidental, special, consequential, or punitive damages resulting from your use of the site or any products purchased through the site.</p>

<h2>Contact Us</h2>
<p>If you have any questions about this Disclaimer, please contact us at <strong>nestdeal@gmail.com</strong>.</p>""",

    "Terms of Use": """<h2>Terms of Use for NestDeal</h2>
<p><strong>Last updated:</strong> August 2026</p>

<h2>Acceptance of Terms</h2>
<p>By accessing and using NestDeal (nestdeal.blogspot.com), you accept and agree to be bound by the terms and provision of this agreement. If you do not agree to these terms, please do not use our website.</p>

<h2>Intellectual Property</h2>
<p>All content on this website, including text, graphics, logos, images, and software, is the property of NestDeal and is protected by copyright laws. You may not reproduce, distribute, or create derivative works from any content on this website without our express written permission.</p>

<h2>Use License</h2>
<p>Permission is granted to temporarily use this website for personal, non-commercial transitory viewing only. This is the grant of a license, not a transfer of title, and under this license you may not:</p>
<ul>
<li>Modify or copy the materials</li>
<li>Use the materials for any commercial purpose</li>
<li>Attempt to decompile or reverse engineer any software contained on the website</li>
<li>Remove any copyright or other proprietary notations from the materials</li>
</ul>

<h2>User Conduct</h2>
<p>When using our website, you agree not to:</p>
<ul>
<li>Use the website for any unlawful purpose</li>
<li>Attempt to gain unauthorized access to any portion of the website</li>
<li>Interfere with or disrupt the website or servers</li>
<li>Use automated systems to access the website without permission</li>
<li>Transmit spam, chain letters, or other unsolicited communications</li>
</ul>

<h2>Product Reviews</h2>
<p>Our product reviews are based on our research and analysis. We strive to provide accurate and honest reviews, but we cannot guarantee that all information is complete or error-free. Always verify product information on the retailer's website before making a purchase.</p>

<h2>Limitation of Liability</h2>
<p>In no event shall NestDeal be liable for any damages (including, without limitation, damages for loss of data or profit, or due to business interruption) arising out of the use or inability to use the materials on NestDeal's website, even if NestDeal has been notified orally or in writing of the possibility of such damage.</p>

<h2>Accuracy of Materials</h2>
<p>The materials appearing on NestDeal could include technical, typographical, or photographic errors. NestDeal does not warrant that any of the materials on its website are accurate, complete, or current.</p>

<h2>Modifications</h2>
<p>NestDeal may revise these terms of use at any time without notice. By using this website, you are agreeing to be bound by the then current version of these terms of use.</p>

<h2>Governing Law</h2>
<p>These terms and conditions are governed by and construed in accordance with the laws of the United States, and you irrevocably submit to the exclusive jurisdiction of the courts in that location.</p>

<h2>Contact Us</h2>
<p>If you have any questions about these Terms of Use, please contact us at <strong>nestdeal@gmail.com</strong>.</p>""",
}


def get_access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    })
    r.raise_for_status()
    return r.json()["access_token"]


def create_page(token, title, content, retries=3):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "kind": "blogger#page",
        "blog": {"id": BLOG_ID},
        "title": title,
        "content": content,
    }
    for attempt in range(retries):
        r = requests.post(
            f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/pages/",
            headers=headers,
            json=body,
        )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 5))
            print(f"  [429] Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(3)
            continue
        r.raise_for_status()
        return r.json()
    raise Exception(f"Failed after {retries} retries for '{title}'")


def main():
    if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        print("ERROR: Missing env vars. Need BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, BLOGGER_BLOG_ID (or BLOG_ID)")
        sys.exit(1)

    print("Getting access token...")
    token = get_access_token()
    print("Token OK.\n")

    results = []
    for title, body in PAGES.items():
        banner = BANNER_TEMPLATE.format(title=title)
        full_content = banner + "\n" + body
        print(f"Creating: {title}...")
        try:
            page = create_page(token, title, full_content)
            page_id = page.get("id", "?")
            url = page.get("url", "?")
            results.append((title, "OK", page_id, url))
            print(f"  Created (id={page_id})")
        except Exception as e:
            results.append((title, "FAILED", str(e), ""))
            print(f"  FAILED: {e}")
        time.sleep(3)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    ok = 0
    for title, status, detail, url in results:
        icon = "+" if status == "OK" else "X"
        print(f"  [{icon}] {title}: {status}")
        if status == "OK":
            print(f"      ID:  {detail}")
            print(f"      URL: {url}")
            ok += 1
        else:
            print(f"      Error: {detail}")
    print(f"\n{ok}/{len(results)} pages created successfully.")


if __name__ == "__main__":
    main()

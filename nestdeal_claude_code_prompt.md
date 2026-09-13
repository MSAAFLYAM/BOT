# NestDeal — Claude Code Master Prompt
## تعليمات شاملة للتنفيذ الفوري — لا تغفل أي خطوة

> **المشروع:** NestDeal Amazon Affiliate Review Bot  
> **البلوق:** https://nestdeal.blogspot.com/  
> **Runtime:** Python 3.13+ · Flask · Telegram Bot · Koyeb Cloud  
> **الملف الرئيسي:** `blogger_api_publisher.py` هو نقطة البداية

---

## السياق الكامل للمشروع

المشروع هو بوت Python يقوم بـ:
1. اكتشاف منتجات Amazon تلقائياً
2. سكرابينج بيانات المنتج (اسم، سعر، تقييم، صور، مراجعات)
3. إثراء البيانات من Reddit ومراجعات Amazon الحرجة
4. توليد مقال مراجعة عبر Groq AI
5. نشر المقال على Blogger + إشعار Telegram

**المشكلات الحالية المطلوب حلها:**
- القالب نفسه يُطبَّق على كل المنتجات → مراجعات تبدو مصنوعة بالجملة
- صور متكررة من نفس المنتج بدون تنوع
- لا يوجد مقارنة حقيقية مع بدائل أرخص/أفضل
- لا يوجد حكم واضح: هل يستحق الشراء أم لا؟
- SEO ضعيف: JSON-LD ناقص، لا FAQPage schema
- الجدولة تعتمد على الحاسوب المحلي فقط

---

## المهمة 1: إعادة كتابة قالب المقال في `blogger_api_publisher.py`

### البنية الجديدة الكاملة للقالب (بالترتيب الدقيق)

استبدل دالة `_build_article()` الحالية بالبنية التالية:

```
SECTION 1 — HERO
- H1: اسم المنتج الكامل
- السعر الحالي بالدولار (مع إظهار السعر الأصلي إن وُجد تخفيض)
- نسبة التخفيض: "Save 23%" إن كان list_price > price
- تقييم النجوم + عدد التقييمات
- Editor Score /10 (مستخرج من AI)
- شارة الحكم: ✅ RECOMMENDED / ⚠️ CHECK ALTERNATIVES / ❌ SKIP
  - RECOMMENDED إذا score >= 7.5
  - CHECK ALTERNATIVES إذا score بين 5.5 و 7.4
  - SKIP إذا score < 5.5
- زر CTA أزرق: "Check Price on Amazon →" مع affiliate tag

SECTION 2 — VERDICT BOX (أول ما يراه الزائر بعد الهيرو)
- خلفية ملونة مميزة (لون مختلف حسب الحكم: أخضر/برتقالي/أحمر)
- عنوان: "Our Verdict"
- جملة 1: لمن هذا المنتج؟
- جملة 2: أفضل ميزة فيه
- جملة 3: أكبر عيب فيه
- سطر أخير: "Better alternative: [اسم البديل] at [سعره]" أو "Best in its category"

SECTION 3 — SCORE BARS (5 معايير)
كل معيار: اسم + شريط تقدم ملون + رقم /10
1. Value for Money     → مقارنة السعر بالمنافسين
2. Build Quality       → جودة المواد والتصنيع
3. Features & Performance → الأداء الفعلي
4. Ease of Use         → سهولة الاستخدام
5. Customer Satisfaction → مستخرج من نسبة التقييمات الإيجابية
+ دائرة Final Score في المنتصف

SECTION 4 — PROS & CONS
يجب أن تكون مستخرجة من مراجعات المشترين الحقيقيين فقط، وليس من AI
- كل Pro: مقتبس مختصر من مراجعة حقيقية [verified buyer]
- كل Con: شكوى متكررة من التقييمات السلبية
- الحد الأدنى: 3 pros + 3 cons
- إذا لم تتوفر مراجعات كافية: استخدم Groq لتوليدها من الـ features

SECTION 5 — WHO IS THIS FOR?
عمودان:
- ✅ Perfect for: [قائمة 3 نقاط]
- ❌ Not ideal for: [قائمة 3 نقاط]

SECTION 6 — SPECIFICATIONS TABLE
جدول HTML منظم بكل المواصفات المتاحة
إذا لم تتوفر مواصفات: اقتصر على: Weight، Dimensions، Brand، Material، Color Options

SECTION 7 — REAL BUYER INSIGHTS
مقسّم إلى قسمين:
A) Amazon Buyers Say:
   - أكثر 3 مدائح تكراراً (مع عدد المرات إن أمكن)
   - أكثر 3 شكاوى تكراراً
B) Community Feedback (Reddit):
   - ملاحظات من مجتمع Reddit إن توفرت
   - إذا لم تتوفر: اكتب "No significant community discussion found"

SECTION 8 — TOP ALTERNATIVES (الأهم للزائر)
جدول مقارنة HTML بـ 3 أعمدة:

| Feature          | [المنتج الحالي] | [البديل الأرخص] | [البديل الأفضل] |
|------------------|-----------------|-----------------|-----------------|
| Price            | $XX             | $YY             | $ZZ             |
| Rating           | ★★★☆☆ (3.8)    | ★★★★☆ (4.2)    | ★★★★★ (4.7)    |
| Best For         | ...             | Budget shoppers | Premium users   |
| Amazon Link      | [Buy]           | [Buy]           | [Buy]           |

- البدائل يجب أن تكون من نفس الفئة على Amazon
- كل رابط بديل يحمل affiliate tag: dazzledeals00-20
- إذا لم تُستخرج بدائل حقيقية: استخدم Groq لاقتراح أسماء منتجات حقيقية
  ثم ابنِ روابط Amazon Search بدلاً من روابط ASIN محددة

SECTION 9 — FAQ
5 أسئلة وأجوبة مستخرجة من:
1. "People Also Ask" في Google (إن توفر سكرابينج)
2. أسئلة المشترين في صفحة Amazon
3. أو يولدها Groq بناءً على اسم المنتج وفئته
يجب أن تكون ذات صلة حقيقية بالمنتج وليست عامة

SECTION 10 — FINAL VERDICT
- درجة إجمالية كبيرة /10
- فقرة 3-4 أسطر: الخلاصة النهائية
- توصية صريحة: "Buy it", "Wait for a sale", "Look elsewhere"
- زر CTA نهائي → Amazon

FOOTER
- إخلاء مسؤولية: "As an Amazon Associate, we earn from qualifying purchases."
- تاريخ آخر تحديث للمقال
```

### كود القالب — HTML/CSS المطلوب

في `blogger_api_publisher.py`، الدالة `_build_article()` يجب أن تُنتج HTML بهذا التصميم:

```python
def _build_article(product: dict, ai_review: str) -> tuple[str, str]:
    """
    Returns: (html_content, article_title)
    
    product dict يجب أن يحتوي على:
    - title, price, list_price (السعر الأصلي), rating, review_count
    - features (list), img_url, secondary_img_url (جديد)
    - clean_url (affiliate URL), category, asin
    - pros (list), cons (list) — مستخرجة من مراجعات حقيقية
    - buyer_insights (dict): {praises: [], complaints: [], reddit: []}
    - alternatives (list of dicts): [{name, price, rating, url}]
    - specs (dict)
    - verdict_score (float 0-10)
    - verdict_badge ('RECOMMENDED'|'CHECK ALTERNATIVES'|'SKIP')
    - faqs (list of dicts): [{question, answer}]
    """
    
    # حساب التخفيض
    discount_html = ""
    if product.get('list_price') and product.get('price'):
        try:
            list_p = float(str(product['list_price']).replace('$','').replace(',',''))
            curr_p = float(str(product['price']).replace('$','').replace(',',''))
            if list_p > curr_p:
                pct = int((list_p - curr_p) / list_p * 100)
                discount_html = f'<span class="nd-discount">Save {pct}%</span>'
        except: pass
    
    # لون الشارة
    score = product.get('verdict_score', 6.0)
    badge = product.get('verdict_badge', 'CHECK ALTERNATIVES')
    badge_color = {'RECOMMENDED': '#22c55e', 'CHECK ALTERNATIVES': '#f59e0b', 'SKIP': '#ef4444'}.get(badge, '#f59e0b')
    verdict_bg = {'RECOMMENDED': '#f0fdf4', 'CHECK ALTERNATIVES': '#fffbeb', 'SKIP': '#fef2f2'}.get(badge, '#fffbeb')
    
    # بناء HTML الكامل — اكتب CSS inline أو في <style> tag
    # يجب أن يكون متوافقاً مع Blogger (لا React، لا JS frameworks)
    # استخدم CSS variables للألوان
    # يجب أن يكون responsive (mobile-first)
    
    # [Claude Code: اكتب HTML الكامل هنا بناءً على البنية أعلاه]
```

---

## المهمة 2: دالة اختيار الصور الذكي في `scraper.py`

### المشكلة
حالياً يتم تمرير كل صور المنتج → تظهر صور متكررة من نفس الزاوية.

### الحل المطلوب

أضف هذه الدالة في `scraper.py` أو `image_processor.py`:

```python
def select_product_images(raw_images: list[str], max_images: int = 2) -> dict:
    """
    من قائمة روابط صور Amazon، اختر صورتين متنوعتين.
    
    منطق الاختيار (بدون API calls):
    - الصورة الرئيسية: أكبر دقة، عادةً تحتوي على '_MAIN_' أو 'main' في الرابط
    - الصورة الثانوية: زاوية مختلفة — ابحث عن: PT01, PT02, PT03, lifestyle, in_use, angle
    - تجنب: صور خلفية بيضاء متشابهة (SX, SY فقط بدون كود تمييز)
    - تجنب: الصور المصغرة (أقل من 400px في الرابط إن ظهر الحجم)
    
    Returns:
        {
            'hero': str,        # الصورة الرئيسية
            'secondary': str,   # الصورة الثانوية (None إن لم تتوفر)
            'all_selected': list # قائمة بالصور المختارة
        }
    """
    if not raw_images:
        return {'hero': '', 'secondary': None, 'all_selected': []}
    
    # أولوية الصورة الرئيسية
    MAIN_INDICATORS = ['_MAIN_', 'main', 'primary', 'cover']
    # أولوية الصورة الثانوية
    SECONDARY_INDICATORS = ['PT01', 'PT02', 'PT03', 'lifestyle', 'in_use', 
                            'angle', 'side', 'back', 'detail', 'feature']
    # ما يجب تجنبه
    AVOID_INDICATORS = ['THUMB', 'thumb', '_SS', '_AC_']
    
    def score_image(url: str, prefer_main: bool) -> int:
        score = 0
        indicators = MAIN_INDICATORS if prefer_main else SECONDARY_INDICATORS
        for ind in indicators:
            if ind in url: score += 10
        for avoid in AVOID_INDICATORS:
            if avoid in url: score -= 20
        # تفضيل الصور ذات الدقة العالية
        for size in ['2000', '1500', '1200', '1000']:
            if size in url: score += 5
        return score
    
    # اختيار الرئيسية
    hero = max(raw_images, key=lambda u: score_image(u, prefer_main=True))
    
    # اختيار الثانوية (مختلفة عن الرئيسية)
    remaining = [img for img in raw_images if img != hero]
    secondary = max(remaining, key=lambda u: score_image(u, prefer_main=False)) if remaining else None
    
    return {
        'hero': hero,
        'secondary': secondary,
        'all_selected': [img for img in [hero, secondary] if img]
    }
```

**التكامل:** في `scraper.py`، بعد استخراج `images`, استدعِ:
```python
selected = select_product_images(product_data.get('images', []))
product_data['img_url'] = selected['hero']
product_data['secondary_img_url'] = selected['secondary']
```

---

## المهمة 3: استخراج البدائل من Amazon (مجاني)

### أضف هذه الدالة في `scraper.py`:

```python
def scrape_alternatives(asin: str, product_title: str, current_price: float) -> list[dict]:
    """
    استخرج بدائل المنتج من Amazon بدون API مدفوع.
    
    الطريقة 1 (الأفضل): استخراج "Customers also viewed" من صفحة ASIN
    الطريقة 2 (احتياطية): بحث Amazon بكلمات مفتاحية من عنوان المنتج
    
    Returns: list of dicts:
    [
        {
            'name': str,
            'price': str,      # "$XX.XX"
            'rating': float,   # 4.2
            'review_count': int,
            'asin': str,
            'url': str,        # Amazon URL + affiliate tag
            'type': 'cheaper'|'better'|'similar'
        }
    ]
    
    المنطق:
    - cheaper: سعره أقل من current_price بـ 15% على الأقل
    - better: تقييمه أعلى من المنتج الحالي بـ 0.3 نقطة على الأقل
    - similar: بديل متقارب
    
    إذا فشل السكرابينج، استخدم Groq لاقتراح أسماء منتجات حقيقية:
    prompt = f"Suggest 2 real Amazon alternatives to '{product_title}' priced around ${current_price}. 
               One cheaper option and one premium option. Return JSON only: 
               [{{'name': '...', 'search_query': '...', 'estimated_price': '...', 'type': 'cheaper|better'}}]"
    ثم ابنِ روابط بحث Amazon:
    url = f"https://www.amazon.com/s?k={quote(search_query)}&tag=dazzledeals00-20"
    """
    alternatives = []
    
    # طبقة 1: استخراج "Customers also viewed" من Jina AI
    try:
        jina_url = f"https://r.jina.ai/https://www.amazon.com/dp/{asin}"
        # سكرابينج وتحليل HTML للعثور على ASINs مرتبطة
        # [Claude Code: نفّذ هنا]
    except Exception as e:
        print(f"[ALTERNATIVES] Jina failed: {e}")
    
    # طبقة 2: Groq fallback
    if len(alternatives) < 2:
        # استخدم Groq لاقتراح بدائل
        # [Claude Code: نفّذ هنا]
        pass
    
    return alternatives[:3]  # ارجع أفضل 3 بدائل كحد أقصى
```

---

## المهمة 4: إثراء بيانات الـ FAQ

### أضف في `review_enrichment.py`:

```python
def generate_faqs(product_title: str, category: str, features: list, 
                  buyer_questions: list = None) -> list[dict]:
    """
    يولد 5 أسئلة وأجوبة ذات صلة بالمنتج.
    
    مصادر بالأولوية:
    1. أسئلة المشترين من صفحة Amazon (community Q&A)
    2. Groq AI يولد أسئلة بناءً على: العنوان + الفئة + الميزات
    
    يجب أن تكون الأسئلة:
    - محددة للمنتج (وليس عامة مثل "Is this worth buying?")
    - تعكس ما يبحث عنه المشترون فعلاً
    - متنوعة: سؤال عن السعر، الجودة، المقارنة، الضمان، الاستخدام
    
    Groq prompt المطلوب:
    "Generate 5 specific FAQ questions and answers for this Amazon product:
    Title: {product_title}
    Category: {category}
    Key features: {features[:5]}
    
    Requirements:
    - Questions must be specific to THIS product, not generic
    - Mix topics: compatibility, durability, value, comparison, use case
    - Answers: 2-3 sentences, factual, based on the features provided
    - Return JSON only: [{{'question': '...', 'answer': '...'}}]"
    
    Returns: [{'question': str, 'answer': str}] — قائمة بـ 5 عناصر
    """
    pass  # [Claude Code: نفّذ هنا]
```

---

## المهمة 5: JSON-LD Schema كامل

### في `blogger_api_publisher.py`، استبدل دالة JSON-LD الحالية بـ:

```python
def build_complete_schema(product: dict, faqs: list, article_url: str = "") -> str:
    """
    يبني JSON-LD كامل يشمل 3 schemas مدمجة في @graph:
    1. Product schema
    2. Review schema  
    3. FAQPage schema
    
    كل الحقول مطلوبة — لا تترك أي حقل فارغاً بدون قيمة افتراضية.
    """
    import json
    from datetime import datetime
    
    price = str(product.get('price', '')).replace('$', '').replace(',', '').strip()
    
    schema = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Product",
                "name": product.get('title', ''),
                "description": product.get('description', product.get('features', [''])[0] if product.get('features') else ''),
                "image": [product.get('img_url', ''), product.get('secondary_img_url', '')],
                "brand": {"@type": "Brand", "name": product.get('brand', 'Unknown')},
                "offers": {
                    "@type": "Offer",
                    "price": price,
                    "priceCurrency": "USD",
                    "availability": "https://schema.org/InStock",
                    "url": product.get('clean_url', ''),
                    "priceValidUntil": (datetime.now().replace(year=datetime.now().year + 1)).strftime("%Y-%m-%d")
                },
                "aggregateRating": {
                    "@type": "AggregateRating",
                    "ratingValue": str(product.get('rating', 4.0)),
                    "reviewCount": str(product.get('review_count', 0)),
                    "bestRating": "5",
                    "worstRating": "1"
                }
            },
            {
                "@type": "Review",
                "itemReviewed": {"@type": "Product", "name": product.get('title', '')},
                "author": {"@type": "Organization", "name": "NestDeal"},
                "datePublished": datetime.now().strftime("%Y-%m-%d"),
                "reviewRating": {
                    "@type": "Rating",
                    "ratingValue": str(product.get('verdict_score', 6.0)),
                    "bestRating": "10",
                    "worstRating": "1"
                },
                "reviewBody": product.get('verdict_summary', '')
            },
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": faq.get('question', ''),
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": faq.get('answer', '')
                        }
                    }
                    for faq in (faqs or [])
                ]
            }
        ]
    }
    
    return f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False, indent=2)}</script>'
```

---

## المهمة 6: دمج pytrends لاختيار منتجات رائجة

### أضف ملف جديد: `trend_discovery.py`

```python
"""
trend_discovery.py
اكتشاف منتجات Amazon الرائجة باستخدام Google Trends (مجاني، بدون API key).
يُستدعى من daily_scheduler.py قبل بدء جلسة النشر.
"""

from pytrends.request import TrendReq
import time
import random

# تصنيفات المنتجات الرئيسية — قابلة للتوسيع
PRODUCT_CATEGORIES = [
    "wireless earbuds", "robot vacuum", "air fryer", "standing desk",
    "mechanical keyboard", "portable charger", "smart watch", "coffee maker",
    "bluetooth speaker", "electric toothbrush", "desk lamp", "webcam"
]

def get_trending_amazon_keywords(categories: list = None, top_n: int = 5) -> list[str]:
    """
    يُرجع قائمة بأكثر المنتجات بحثاً هذا الأسبوع.
    
    Args:
        categories: قائمة فئات للبحث (تستخدم PRODUCT_CATEGORIES إذا لم تُحدَّد)
        top_n: عدد الكلمات المفتاحية المُرجَعة
    
    Returns: ['wireless earbuds 2025', 'best robot vacuum under 200', ...]
    
    الخطوات:
    1. للكل category في القائمة، اطلب related_queries من pytrends
    2. استخرج الاستعلامات المرتبطة الأعلى
    3. رتّبها حسب حجم البحث
    4. أضف "amazon" أو "review" أو "best" للاستعلام لتحسين الاكتشاف
    
    مهم: أضف delay بين الطلبات (2-4 ثوانٍ) لتجنب rate limiting
    """
    pytrends = TrendReq(hl='en-US', tz=360)
    trending_keywords = []
    
    cats = categories or random.sample(PRODUCT_CATEGORIES, min(3, len(PRODUCT_CATEGORIES)))
    
    for category in cats:
        try:
            pytrends.build_payload([category], timeframe='now 7-d', geo='US')
            related = pytrends.related_queries()
            
            if related.get(category) and related[category].get('top') is not None:
                top_queries = related[category]['top']['query'].tolist()[:3]
                trending_keywords.extend([f"{q}" for q in top_queries])
            
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            print(f"[TRENDS] Failed for '{category}': {e}")
            continue
    
    return trending_keywords[:top_n]


def build_amazon_search_url(keyword: str, affiliate_tag: str = "dazzledeals00-20") -> str:
    """
    يبني رابط بحث Amazon من كلمة مفتاحية.
    """
    from urllib.parse import quote
    return f"https://www.amazon.com/s?k={quote(keyword)}&tag={affiliate_tag}"


def get_trending_products_for_scheduler() -> list[str]:
    """
    الدالة الرئيسية التي يستدعيها daily_scheduler.py
    تُرجع قائمة روابط Amazon جاهزة للمعالجة.
    """
    keywords = get_trending_amazon_keywords(top_n=5)
    urls = [build_amazon_search_url(kw) for kw in keywords]
    print(f"[TRENDS] Found {len(urls)} trending products: {keywords}")
    return urls
```

### التكامل مع `daily_scheduler.py`:
```python
# في بداية كل جلسة نشر، استبدل الكلمات المفتاحية الثابتة بـ:
from trend_discovery import get_trending_products_for_scheduler

# إذا لم تتوفر AUTO_KEYWORDS من .env
if not auto_keywords:
    urls_to_process = get_trending_products_for_scheduler()
```

---

## المهمة 7: GitHub Actions — أتمتة بدون حاسوب

### أنشئ الملف: `.github/workflows/daily_trigger.yml`

```yaml
name: NestDeal Daily Auto-Post Trigger
# يُشغَّل كل يوم الساعة 8:00 صباحاً UTC (10:00 صباحاً بتوقيت المغرب)

on:
  schedule:
    - cron: '0 8 * * *'
  workflow_dispatch:  # يسمح بالتشغيل اليدوي من GitHub UI

jobs:
  trigger-daily-post:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    
    steps:
      - name: Trigger NestDeal Bot Daily Session
        run: |
          response=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "${{ secrets.KOYEB_APP_URL }}/trigger-daily" \
            -H "Authorization: Bearer ${{ secrets.TRIGGER_SECRET }}" \
            -H "Content-Type: application/json" \
            -d '{"source": "github_actions", "max_articles": 3}')
          
          echo "Response code: $response"
          if [ "$response" != "200" ]; then
            echo "❌ Trigger failed with code $response"
            exit 1
          fi
          echo "✅ Daily session triggered successfully"
      
      - name: Health Check
        if: always()
        run: |
          curl -s "${{ secrets.KOYEB_APP_URL }}/health" || echo "Health check failed"
```

### أضف endpoint في `main.py`:
```python
@flask_app.route('/trigger-daily', methods=['POST'])
def trigger_daily():
    """Endpoint لـ GitHub Actions"""
    auth = request.headers.get('Authorization', '')
    secret = os.getenv('TRIGGER_SECRET', '')
    
    if not secret or auth != f'Bearer {secret}':
        return jsonify({'error': 'Unauthorized'}), 401
    
    # استدعِ دالة daily_scheduler مباشرة
    import threading
    thread = threading.Thread(target=run_daily_session, daemon=True)
    thread.start()
    
    return jsonify({'status': 'triggered', 'source': request.json.get('source')}), 200
```

### أضف لـ `.env.example`:
```
TRIGGER_SECRET=your_random_secret_here_change_this
```

### أضف لـ GitHub Secrets (في إعدادات الـ repo):
- `KOYEB_APP_URL`: رابط Koyeb الخاص بك (مثل: `https://nestdeal-xxx.koyeb.app`)
- `TRIGGER_SECRET`: نفس القيمة في `.env`

---

## المهمة 8: تحسين `review_enrichment.py` — استخراج Pros/Cons حقيقية

### استبدل أو عدّل دالة استخراج المراجعات:

```python
def extract_pros_cons_from_reviews(reviews: list[dict], rating: float) -> dict:
    """
    يستخرج pros وcons حقيقية من مراجعات المشترين.
    
    Args:
        reviews: قائمة المراجعات [{text, rating, title, verified}]
        rating: التقييم الإجمالي للمنتج
    
    الخوارزمية:
    1. المراجعات 4-5 نجوم → مصدر الـ pros
    2. المراجعات 1-2 نجوم → مصدر الـ cons
    3. المراجعات 3 نجوم → قد تحتوي على كليهما
    
    إذا توفرت أقل من 5 مراجعات:
    - استخدم Groq مع prompt واضح يطلب pros/cons بناءً على features
    
    Returns:
    {
        'pros': ['سرعة الشحن ممتازة', 'جودة البناء متينة', ...],
        'cons': ['البطارية تستنزف بسرعة', 'التطبيق المرافق بطيء', ...],
        'source': 'real_reviews' | 'ai_generated'
    }
    
    مهم: كل pro/con يجب أن يكون جملة واحدة واضحة، لا أكثر.
    لا تستخدم رموز HTML في النصوص (&#10003; ممنوع).
    """
    positive_reviews = [r for r in reviews if r.get('rating', 0) >= 4]
    negative_reviews = [r for r in reviews if r.get('rating', 0) <= 2]
    
    pros = []
    cons = []
    
    # استخراج من المراجعات الحقيقية
    if len(positive_reviews) >= 3:
        # استخدم Groq لتلخيص أبرز نقاط الإيجاب من المراجعات الإيجابية
        reviews_text = "\n".join([r.get('text', '')[:200] for r in positive_reviews[:10]])
        # [Claude Code: استدعِ Groq هنا لاستخراج 3-5 pros]
        pass
    
    if len(negative_reviews) >= 2:
        # استخدم Groq لتلخيص أبرز الشكاوى من المراجعات السلبية
        reviews_text = "\n".join([r.get('text', '')[:200] for r in negative_reviews[:10]])
        # [Claude Code: استدعِ Groq هنا لاستخراج 3-5 cons]
        pass
    
    # Fallback إذا لم تتوفر مراجعات كافية
    source = 'real_reviews' if (pros and cons) else 'ai_generated'
    
    return {'pros': pros or [], 'cons': cons or [], 'source': source}
```

---

## المهمة 9: تثبيت التبعيات الجديدة

### عدّل `requirements.txt` بإضافة:

```
# إضافات جديدة
pytrends==4.9.2
camoufox>=0.4.0
```

### في `Dockerfile`، تأكد من:
```dockerfile
RUN pip install --no-cache-dir -r requirements.txt
# pytrends لا تحتاج API key — تعمل مباشرة
```

---

## المهمة 10: CSS مدمج في القالب

### في `blogger_api_publisher.py`، أضف CSS الكامل للقالب الجديد:

```python
ARTICLE_CSS = """
<style>
:root {
  --nd-green: #22c55e;
  --nd-orange: #f59e0b;
  --nd-red: #ef4444;
  --nd-blue: #3b82f6;
  --nd-dark: #1e293b;
  --nd-gray: #64748b;
  --nd-light: #f8fafc;
  --nd-border: #e2e8f0;
  --nd-radius: 12px;
  --nd-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
}

/* HERO */
.nd-hero { background: var(--nd-light); border-radius: var(--nd-radius); padding: 24px; margin-bottom: 24px; }
.nd-title { font-size: 1.75rem; font-weight: 700; color: var(--nd-dark); margin: 0 0 12px; }
.nd-price-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.nd-price { font-size: 2rem; font-weight: 800; color: var(--nd-dark); }
.nd-list-price { font-size: 1rem; color: var(--nd-gray); text-decoration: line-through; }
.nd-discount { background: var(--nd-red); color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; font-weight: 700; }
.nd-badge { display: inline-block; padding: 6px 16px; border-radius: 20px; font-weight: 700; font-size: 0.9rem; color: white; }
.nd-badge-recommended { background: var(--nd-green); }
.nd-badge-check { background: var(--nd-orange); }
.nd-badge-skip { background: var(--nd-red); }
.nd-cta { display: block; background: var(--nd-blue); color: white !important; text-align: center; padding: 14px 24px; border-radius: var(--nd-radius); font-weight: 700; font-size: 1.1rem; text-decoration: none !important; margin: 16px 0; }
.nd-cta:hover { background: #2563eb; }

/* VERDICT BOX */
.nd-verdict-box { border-radius: var(--nd-radius); padding: 20px 24px; margin: 24px 0; border-left: 5px solid; }
.nd-verdict-box h3 { margin: 0 0 12px; font-size: 1.2rem; }
.nd-verdict-box p { margin: 6px 0; font-size: 0.95rem; }

/* SCORE BARS */
.nd-scores { margin: 24px 0; }
.nd-score-item { margin-bottom: 16px; }
.nd-score-label { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 0.9rem; font-weight: 600; }
.nd-score-bar { height: 8px; background: var(--nd-border); border-radius: 4px; overflow: hidden; }
.nd-score-fill { height: 100%; border-radius: 4px; transition: width 0.6s ease; }
.nd-final-score { text-align: center; margin: 24px 0; }
.nd-score-circle { width: 80px; height: 80px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; color: white; }

/* PROS CONS */
.nd-pros-cons { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 24px 0; }
.nd-pros, .nd-cons { background: var(--nd-light); border-radius: var(--nd-radius); padding: 16px; }
.nd-pros h4 { color: var(--nd-green); margin: 0 0 12px; }
.nd-cons h4 { color: var(--nd-red); margin: 0 0 12px; }
.nd-pros li::before { content: "✓ "; color: var(--nd-green); font-weight: 700; }
.nd-cons li::before { content: "✗ "; color: var(--nd-red); font-weight: 700; }
.nd-pros ul, .nd-cons ul { list-style: none; padding: 0; margin: 0; }
.nd-pros li, .nd-cons li { padding: 6px 0; font-size: 0.9rem; border-bottom: 1px solid var(--nd-border); }
.nd-pros li:last-child, .nd-cons li:last-child { border-bottom: none; }

/* ALTERNATIVES TABLE */
.nd-alt-table { width: 100%; border-collapse: collapse; margin: 24px 0; font-size: 0.9rem; }
.nd-alt-table th { background: var(--nd-dark); color: white; padding: 10px 14px; text-align: left; }
.nd-alt-table td { padding: 10px 14px; border-bottom: 1px solid var(--nd-border); }
.nd-alt-table tr:nth-child(even) { background: var(--nd-light); }
.nd-alt-table tr:first-child td { font-weight: 700; background: #eff6ff; }
.nd-alt-link { color: var(--nd-blue); text-decoration: none; font-weight: 600; }

/* FAQ */
.nd-faq { margin: 24px 0; }
.nd-faq-item { border: 1px solid var(--nd-border); border-radius: var(--nd-radius); margin-bottom: 8px; overflow: hidden; }
.nd-faq-q { padding: 14px 16px; font-weight: 600; background: var(--nd-light); cursor: pointer; }
.nd-faq-a { padding: 14px 16px; font-size: 0.9rem; color: var(--nd-gray); }

/* SPECS TABLE */
.nd-specs { width: 100%; border-collapse: collapse; margin: 16px 0; }
.nd-specs td { padding: 8px 12px; border-bottom: 1px solid var(--nd-border); font-size: 0.9rem; }
.nd-specs td:first-child { font-weight: 600; color: var(--nd-dark); width: 35%; }

/* MOBILE */
@media (max-width: 600px) {
  .nd-pros-cons { grid-template-columns: 1fr; }
  .nd-title { font-size: 1.3rem; }
  .nd-price { font-size: 1.5rem; }
  .nd-alt-table { font-size: 0.8rem; }
  .nd-alt-table th, .nd-alt-table td { padding: 8px; }
}

/* AFFILIATE DISCLAIMER */
.nd-disclaimer { font-size: 0.75rem; color: var(--nd-gray); border-top: 1px solid var(--nd-border); margin-top: 32px; padding-top: 16px; }
</style>
"""
```

---

## قائمة التحقق النهائية — يجب إنجاز كل هذا

- [ ] **1.** إعادة كتابة `_build_article()` في `blogger_api_publisher.py` بالقالب الجديد الكامل
- [ ] **2.** إضافة `select_product_images()` في `scraper.py` أو `image_processor.py`
- [ ] **3.** استدعاء `select_product_images()` في كل أماكن استخراج الصور
- [ ] **4.** إضافة `scrape_alternatives()` في `scraper.py`
- [ ] **5.** إضافة `generate_faqs()` في `review_enrichment.py`
- [ ] **6.** استبدال دالة JSON-LD بـ `build_complete_schema()` في `blogger_api_publisher.py`
- [ ] **7.** إنشاء `trend_discovery.py` كاملاً
- [ ] **8.** تعديل `daily_scheduler.py` لاستخدام `trend_discovery`
- [ ] **9.** إنشاء `.github/workflows/daily_trigger.yml`
- [ ] **10.** إضافة endpoint `/trigger-daily` في `main.py`
- [ ] **11.** إضافة `extract_pros_cons_from_reviews()` في `review_enrichment.py`
- [ ] **12.** إضافة `ARTICLE_CSS` في `blogger_api_publisher.py`
- [ ] **13.** تحديث `requirements.txt` بإضافة pytrends
- [ ] **14.** إضافة `TRIGGER_SECRET` لـ `.env.example`
- [ ] **15.** اختبار القالب الجديد بمنتج واحد قبل تفعيل الجدول التلقائي

---

## ملاحظات تقنية مهمة لـ Claude Code

1. **لا تكسر الكود الحالي** — كل التعديلات يجب أن تكون backward compatible
2. **affiliate tag ثابت** في كل رابط: `tag=dazzledeals00-20` + `rel="nofollow sponsored noopener"`
3. **Groq هو الـ AI الوحيد** — لا تستخدم OpenAI API في أي وظيفة جديدة
4. **السعر دائماً بالدولار** — تحويل MAD/EUR/GBP → USD قبل العرض
5. **HTML للـ Blogger** — لا React، لا Vue، لا JS frameworks — فقط HTML/CSS/JS vanilla
6. **لا تكسر mobile layout** — كل عنصر جديد يجب أن يكون responsive
7. **الـ CSS يجب أن يكون داخل `<style>` tag** في بداية كل مقال (Blogger لا يدعم external CSS)
8. **التسمية:** كل class يبدأ بـ `nd-` لتجنب التعارض مع theme الـ Blogger
9. **Groq rate limits:** أضف `time.sleep(1)` بين الاستدعاءات المتعددة
10. **اختبر في Draft أولاً** باستخدام `publish_now=False` قبل النشر الفعلي

---

## بيئة التطوير

```
Local: C:\Users\user\Desktop\bot-working
Python: C:\Python314\python.exe
Cloud: Koyeb (auto-deploy من GitHub)
Blog: nestdeal.blogspot.com
Affiliate: dazzledeals00-20
```

---

*هذا الملف يمثل التعليمات الكاملة — نفّذ كل مهمة بالترتيب دون إغفال أي تفصيل.*

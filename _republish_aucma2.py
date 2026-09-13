import re, requests, time
from dotenv import load_dotenv
load_dotenv()
from blogger_api_publisher import _get_access_token, BLOGGER_BASE, BLOG_ID, _build_article, publish_post, _map_labels
import scraper
t=_get_access_token()
# find new Aucma (with suffix)
all_posts=[]
pt=None
while True:
    p={'maxResults':50,'status':'live'}
    if pt: p['pageToken']=pt
    r=requests.get(f'{BLOGGER_BASE}/blogs/{BLOG_ID}/posts', headers={'Authorization': f'Bearer {t}'}, params=p, timeout=15)
    if r.status_code!=200: break
    d=r.json()
    all_posts.extend(d.get('items',[]))
    pt=d.get('nextPageToken')
    if not pt: break
target=None
for post in all_posts:
    if 'aucma-stand-mixer' in post.get('url','') and '01034202106' in post.get('url',''):
        target=post
        break
if not target:
    print('new aucma not found, trying any aucma')
    for post in all_posts:
        if 'aucma-stand-mixer' in post.get('url',''):
            target=post
            break
print('target', target['title'][:50], target['id'])
html=target.get('content','')
asin=re.search(r'amazon\.com/dp/([A-Z0-9]{10})', html).group(1)
print('asin', asin)
m=re.search(r'<img[^>]+src="([^"]+)"', html)
u=m.group(1) if m else ''
print('img', u[:80])
product={'title': target['title'], 'asin': asin, 'clean_url': f'https://www.amazon.com/dp/{asin}', 'aff_link': scraper.build_affiliate_url(f'https://www.amazon.com/dp/{asin}'), 'img_url': u, 'all_images': [u] if 'm.media-amazon' in u else [], 'price': '$99', 'rating': 4.6, 'review_count': 15201, 'features': ['6.5-QT Tilt-Head Bowl','660W motor']}
new_title, new_html = _build_article(product, '')
print('thumbs', new_html.count('rvw-thumb'), 'has rvw-main-img', 'rvw-main-img' in new_html)
res=publish_post(product=product, description='', labels=_map_labels(product['title']), html_content=new_html, title=new_title, publish_now=True)
print('publish', res)
if res.get('status')=='success':
    time.sleep(3)
    del_r=requests.delete(f'{BLOGGER_BASE}/blogs/{BLOG_ID}/posts/{target["id"]}', headers={'Authorization': f'Bearer {t}'}, timeout=15)
    print('delete', del_r.status_code)

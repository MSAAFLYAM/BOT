from dotenv import load_dotenv
load_dotenv()
from blogger_api_publisher import _build_article
p={'title':'GHome Smart Plug WiFi Test Review','price':'$18','aff_link':'https://www.amazon.com/dp/B0TEST?tag=dazzledeals00-20','img_url':'https://m.media-amazon.com/images/I/71test.jpg','all_images':['https://m.media-amazon.com/images/I/71test.jpg'],'rating':4.5,'review_count':1200,'features':['WiFi','Alexa','15A']}
t,h=_build_article(p,'')
print('has span', 'data-nd-review' in h)
import re
m=re.search(r'<span data-nd-review[^>]+>', h)
print(m.group(0)[:500] if m else 'no span')
print(h.count('data-nd-review'))

# Content Restructuring Instructions — Amazon Review Site

> This file is addressed directly to the model that generates content inside the automation project (main.py). Apply these rules to every step that generates text, titles, tags, or HTML templates.

## 0. Current Problem (must stop immediately)
- The site currently copies the Amazon product title verbatim as the post title.
- This makes the site look like a copy of Amazon instead of an independent review site, hurts SEO ranking, and reduces visitor trust.
- **From now on, `amazon_title` must never be used directly as the post title.**

## 1. Title Generator (applies to every single-product review page)
When generating any single-product review page, use the following prompt instead of copying the title directly:

```
You are a content writer specialized in Amazon product reviews.
You have: original Amazon title = {amazon_title}
Key specs = {specs}
Category = {category}

Write a new review article title that meets these conditions:
- Does not repeat the Amazon title verbatim in any form
- Reflects the user's search intent (e.g. "Best [category] for [use case] under [budget]")
- Reads as an independent review/comparison, not a product page
- Length between 50 and 65 characters (for search snippet optimization)
- In the target article language (Arabic/French/English depending on the channel)

Return only the title, with no extra explanation.
```

## 2. Automatic Tagging (Tags / Labels)
After generating each article, add a tag-extraction step and inject the result into the `labels` field when publishing via the Blogger API:

```
Extract 3 to 5 short tags from this article representing:
- The product's general category
- Its most prominent feature
- The price range or target audience (e.g. "budget-friendly", "professional")

Return the tags as a comma-separated list only.
```

## 3. Product Comparison Template (5 products) — a template fully separate from the single-review template
This template is used only when the pipeline requests a comparison page for a group of 5 products within the same category.

### Title Structure (Hook Title)
```
Write an attention-grabbing title for a page comparing 5 products in the {category} category.
The title must speak directly to the visitor's intent, for example:
- "Looking for a [category] with these specs and this budget? You're in the right place"
- "Best 5 [category] for [year] — a complete budget-based comparison"
Do not use individual product names in the title.
```

### Page Structure (fixed HTML sections, the AI only fills in the content)
1. **Hook intro**: a short paragraph (2-3 sentences) speaking directly to the visitor ("you"), describing their need/problem, and promising a clear solution in the article.
2. **Quick comparison table**: one row per product × columns (approximate price, standout feature, best suited for...).
3. **Detailed card for each product (×5)**:
   - Strengths (2-3 points)
   - One honest weakness (increases credibility)
   - A "best suited for someone looking for..." sentence
   - The affiliate link/button for that specific product
4. **Recommendation conclusion**: a summary like "If your budget is limited, choose [X]; if you want the best regardless of budget, choose [Y]; for daily use, choose [Z]."

> Technical note: the model must return the content as structured JSON (title, intro, comparison_table, product_cards[], conclusion) to be injected into a fixed HTML template in the code — not have the model generate free-form HTML, to avoid breaking the template's design.

## 4. Core Rule for Every Generated Text (applies to both single reviews and comparison pages)
Goal: convince the visitor to click the affiliate link and go to Amazon voluntarily, even though they could go directly to Amazon without visiting the site. Every piece of text must therefore add **real value** that isn't already on the Amazon page itself:

```
When writing any review or comparison, do not just copy or rephrase Amazon's specs. Always add at least one of the following:
- A practical comparison with a similar or alternative product
- A realistic use-case scenario (e.g. "great for frequent travelers because it's lightweight")
- A warning about a common flaw or a mistake buyers often make
- An honest tip: "watch out for X before buying" or "make sure of Y before ordering"

Always place the affiliate link within a clear recommendation context, not just a "Buy Now" button,
e.g.: "After this comparison, this is the best option for your budget → [affiliate link]"

Place the affiliate link in more than one natural spot within the text: after each key strength point, and in the conclusion.
```

## 5. Execution Priority Summary
1. Stop using the Amazon title directly as the post title — activate the title generator (Section 1).
2. Enable automatic tag extraction and injection (Section 2).
3. Build a new, separate HTML template for comparison pages (Section 3), with its own dedicated JSON generation flow.
4. Inject the "added value + affiliate link phrasing" rule (Section 4) into every existing content-generation prompt, for both single reviews and comparison pages.

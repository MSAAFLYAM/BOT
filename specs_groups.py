"""
specs_groups.py
Builds a specs table organized into groups (like GSMArena) by product category.
Values come from scraped features + Groq to fill gaps.
"""
import json


# Template of groups per category
SPEC_GROUPS_BY_CATEGORY = {
    "earbuds": [
        {"group": "GENERAL", "rows": ["Brand", "Model", "Release Year", "Warranty"]},
        {"group": "AUDIO", "rows": ["Driver Size", "Frequency Response", "Impedance", "Noise Cancellation", "Sound Profile"]},
        {"group": "BATTERY", "rows": ["Earbud Battery", "Case Battery", "Total Battery Life", "Charging Time", "Fast Charge"]},
        {"group": "CONNECTIVITY", "rows": ["Bluetooth Version", "Range", "Codec Support", "Multipoint"]},
        {"group": "DESIGN", "rows": ["Weight (each)", "IPX Rating", "Colors", "Ear Tip Sizes"]},
        {"group": "IN THE BOX", "rows": ["Earbuds", "Charging Case", "Cable", "Ear Tips", "Manual"]}
    ],
    "headphones": [
        {"group": "GENERAL", "rows": ["Brand", "Model", "Type", "Connectivity", "Warranty"]},
        {"group": "AUDIO", "rows": ["Driver Size", "Frequency Response", "Impedance", "Sensitivity", "Noise Cancellation"]},
        {"group": "BATTERY", "rows": ["Battery Life", "Charging Time", "Fast Charge"]},
        {"group": "DESIGN", "rows": ["Weight", "Foldable", "IPX Rating", "Colors"]},
        {"group": "IN THE BOX", "rows": ["Headphones", "Cable", "Case", "Adapter"]}
    ],
    "smart_home": [
        {"group": "GENERAL", "rows": ["Brand", "Model", "Compatible With", "Warranty"]},
        {"group": "CONNECTIVITY", "rows": ["Protocol", "WiFi Band", "Bluetooth", "Hub Required"]},
        {"group": "POWER", "rows": ["Power Source", "Battery Life", "Wattage"]},
        {"group": "SPECS", "rows": ["Dimensions", "Weight", "Material", "IP Rating"]},
        {"group": "COMPATIBILITY", "rows": ["Alexa", "Google Home", "Apple HomeKit", "Matter"]}
    ],
    "default": [
        {"group": "GENERAL", "rows": ["Brand", "Model", "Warranty"]},
        {"group": "SPECIFICATIONS", "rows": ["Material", "Dimensions", "Weight", "Color Options"]},
        {"group": "IN THE BOX", "rows": ["Main Item", "Accessories", "Documentation"]}
    ]
}


def detect_category(product_title: str, category_hint: str = "") -> str:
    """Determine product category from the title."""
    title_lower = (product_title + " " + category_hint).lower()
    if any(k in title_lower for k in ['earbuds', 'earbud', 'tws', 'true wireless']):
        return 'earbuds'
    if any(k in title_lower for k in ['headphone', 'headset', 'over-ear', 'on-ear']):
        return 'headphones'
    if any(k in title_lower for k in ['smart home', 'hub', 'switch', 'thermostat', 'doorbell', 'camera', 'lock']):
        return 'smart_home'
    return 'default'


def build_specs_table_html(product: dict, specs_values: dict) -> str:
    """
    Builds HTML for a specs table grouped by category.

    Args:
        product: product data (title, category, features)
        specs_values: dict {label: value}
                      Values come from:
                      1. scraped Amazon features
                      2. Groq for completing gaps
                      3. 'N/A' if not available (never invent a value)

    Returns: HTML string
    """
    category = detect_category(product.get('title', ''), product.get('category', ''))
    groups = SPEC_GROUPS_BY_CATEGORY.get(category, SPEC_GROUPS_BY_CATEGORY['default'])

    html_parts = ['<div class="nd3-specs-container">']

    for group in groups:
        rows_html = []
        for label in group['rows']:
            value = specs_values.get(label, specs_values.get(label.lower(), 'N/A'))
            highlight = ' nd3-spec-highlight' if value not in ['N/A', '', None] and label in ['Battery Life', 'Total Battery Life', 'Noise Cancellation', 'IPX Rating'] else ''
            rows_html.append(f'''
            <tr class="nd3-spec-row{highlight}">
                <td class="nd3-spec-label">{label}</td>
                <td class="nd3-spec-value">{value if value else 'N/A'}</td>
            </tr>''')

        html_parts.append(f'''
        <div class="nd3-spec-group">
            <div class="nd3-spec-group-header">{group["group"]}</div>
            <table class="nd3-specs-table">
                {''.join(rows_html)}
            </table>
        </div>''')

    html_parts.append('</div>')
    return '\n'.join(html_parts)


def extract_specs_with_groq(product_title: str, features: list, category: str) -> dict:
    """
    Uses Groq to fill the specs table from scraped features.

    Groq prompt:
    "Extract specifications from these product features and return ONLY valid JSON.
    Product: {title}
    Features: {features}

    Fill these fields (use 'N/A' if not mentioned, NEVER invent values):
    {list of spec labels for this category}

    Return format: {{'Brand': 'X', 'Battery Life': 'Y', ...}}"
    """
    from review_enrichment import call_groq_safe  # unified safe Groq helper

    groups = SPEC_GROUPS_BY_CATEGORY.get(category, SPEC_GROUPS_BY_CATEGORY['default'])
    all_labels = [row for group in groups for row in group['rows']]

    prompt = f"""Extract product specifications and return ONLY a JSON object.
Product: {product_title}
Features list: {features[:10]}

Fill ONLY these fields (use "N/A" if not mentioned, NEVER invent values):
{json.dumps({label: "?" for label in all_labels})}

Return ONLY the JSON object, nothing else."""

    result = call_groq_safe(prompt, expect_json=True)
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {label: 'N/A' for label in all_labels}
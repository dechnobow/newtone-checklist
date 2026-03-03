import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import json
import re
import os

JST = timezone(timedelta(hours=9))
today = datetime.now(JST).strftime('%Y-%m-%d')

BASE_URL = 'https://newtone-records.com'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'html.parser')
    except Exception as e:
        print(f'Error fetching {url}: {e}')
        return None

def parse_products(soup, category):
    records = []
    if not soup:
        return records

    items = soup.select('ul.products li, .product-list li, li.product')
    if not items:
        items = soup.select('li')

    for item in items:
        try:
            title_el = item.select_one('a[href*="/product/"]')
            if not title_el:
                continue

            href = title_el.get('href', '')
            if not href.startswith('http'):
                href = BASE_URL + '/' + href.lstrip('/')

            m = re.search(r'/product/(\d+)', href)
            if not m:
                continue
            rid = 't' + m.group(1).lstrip('0')

            img_el = item.select_one('img')
            img = ''
            if img_el:
                img = img_el.get('src', '')
                if img and not img.startswith('http'):
                    img = BASE_URL + img

            artist_el = item.select_one('h3 a, h3, .artist a, .artist')
            artist = artist_el.get_text(strip=True) if artist_el else ''

            fmt_el = item.select_one('.format, .type, span.cat')
            fmt = fmt_el.get_text(strip=True) if fmt_el else ''

            label_el = item.select_one('.label a, .label')
            label = label_el.get_text(strip=True) if label_el else ''

            title = title_el.get_text(strip=True)
            if not title:
                continue

            records.append({
                'id':       rid,
                'artist':   artist,
                'title':    title,
                'label':    label,
                'format':   fmt,
                'genre':    '',
                'img':      img,
                'url':      href,
                'used':     category == 'used',
                'preorder': category == 'preorder',
            })
        except Exception as e:
            print(f'Parse error: {e}')

    seen = set()
    unique = []
    for r in records:
        if r['id'] not in seen:
            seen.add(r['id'])
            unique.append(r)
    return unique

def scrape_all():
    categories = {
        'thisweek': '/store/thisweek/',
        'new':      '/store/',
        'preorder': '/store/pre/',
        'used':     '/store/used/',
    }
    all_groups = []
    for cat, path in categories.items():
        url = BASE_URL + path
        print(f'Scraping {cat}: {url}')
        soup = fetch(url)
        if soup:
            sample = soup.select_one('a[href*="/product/"]')
            if sample:
                print(f'  Sample link: {sample.get("href","")}')
            else:
                print(f'  WARNING: No product links found')
        records = parse_products(soup, cat)
        print(f'  Found {len(records)} items')
        if records:
            all_groups.append({
                'date': today,
                'category': cat,
                'records': records
            })
    return all_groups

def load_existing_data(html_path):
    if not os.path.exists(html_path):
        print('index.html not found')
        return []
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    m = re.search(r'const rawData = (\[[\s\S]*?\]);\s*\n', content)
    if not m:
        print('rawData not found in index.html')
        return []

    raw = m.group(1)

    # まずJSON形式で試す
    try:
        data = json.loads(raw)
        print(f'Loaded {len(data)} existing groups (JSON)')
        return data
    except:
        pass

    # JS形式をJSONに変換
    try:
        json_str = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', raw)
        json_str = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', json_str)
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        data = json.loads(json_str)
        print(f'Loaded {len(data)} existing groups (JS converted)')
        return data
    except Exception as e:
        print(f'Failed to parse existing data: {e}')
        return []

def merge_data(existing, new_groups):
    merged = [g for g in existing if g.get('date') != today]
    merged.extend(new_groups)
    merged.sort(key=lambda g: (g.get('date', ''), g.get('category', '')), reverse=True)
    cutoff = (datetime.now(JST) - timedelta(days=90)).strftime('%Y-%m-%d')
    merged = [g for g in merged if g.get('date', '') >= cutoff]
    print(f'Total groups after merge: {len(merged)}')
    return merged

def update_html(data):
    html_path = 'index.html'
    if not os.path.exists(html_path):
        print('index.html not found')
        return

    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    data_json = json.dumps(data, ensure_ascii=False, indent=2)
    new_content = re.sub(
        r'const rawData = \[[\s\S]*?\];\s*\n',
        f'const rawData = {data_json};\n',
        content
    )

    if new_content == content:
        print('WARNING: rawData pattern not matched')
    else:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print('Updated index.html successfully')

if __name__ == '__main__':
    print(f'Date: {today}')
    new_groups = scrape_all()
    if new_groups:
        existing = load_existing_data('index.html')
        merged = merge_data(existing, new_groups)
        update_html(merged)
        print('Done.')
    else:
        print('No data scraped — skipping update')

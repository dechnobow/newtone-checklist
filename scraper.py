import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import json
import re
import os

JST = timezone(timedelta(hours=9))
today = datetime.now(JST).strftime('%Y-%m-%d')

BASE_URL = 'https://newtone-records.com'
HEADERS = {'User-Agent': 'Mozilla/5.0'}

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
    for item in soup.select('.product-item, .item'):
        try:
            artist_el = item.select_one('h3, .artist')
            title_el  = item.select_one('a[href*="/product/"]')
            img_el    = item.select_one('img')
            fmt_el    = item.select_one('.format, .type')
            label_el  = item.select_one('.label')

            if not title_el:
                continue

            href = title_el.get('href','')
            if not href.startswith('http'):
                href = BASE_URL + '/' + href.lstrip('/')

            img = ''
            if img_el:
                img = img_el.get('src','')
                if not img.startswith('http'):
                    img = BASE_URL + img

            # IDをURLから抽出
            m = re.search(r'/product/(\d+)', href)
            rid = m.group(1) if m else href

            records.append({
                'id':     rid,
                'artist': artist_el.get_text(strip=True) if artist_el else '',
                'title':  title_el.get_text(strip=True),
                'label':  label_el.get_text(strip=True) if label_el else '',
                'format': fmt_el.get_text(strip=True) if fmt_el else '',
                'genre':  '',
                'img':    img,
                'url':    href,
                'used':   category == 'used',
                'preorder': category == 'preorder',
            })
        except Exception as e:
            print(f'Parse error: {e}')
    return records

def scrape_all():
    categories = {
        'thisweek': '/store/thisweek/',
        'new':      '/store/',
        'preorder': '/store/pre/',
        'used':     '/store/used/',
    }
    all_groups = []
    for cat, path in categories.items():
        print(f'Scraping {cat}...')
        soup = fetch(BASE_URL + path)
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
        return []
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'const rawData = (\[.*?\]);', content, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except:
        return []

def merge_data(existing, new_groups):
    # 今日のデータを既存から削除して新データで置き換え
    existing = [g for g in existing if g.get('date') != today]
    existing.extend(new_groups)
    # 日付降順でソート、最大90日分保持
    existing.sort(key=lambda g: g.get('date',''), reverse=True)
    cutoff = (datetime.now(JST) - timedelta(days=90)).strftime('%Y-%m-%d')
    existing = [g for g in existing if g.get('date','') >= cutoff]
    return existing

def update_html(data):
    html_path = 'index.html'
    data_json = json.dumps(data, ensure_ascii=False, indent=2)

    if os.path.exists(html_path):
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = re.sub(
            r'const rawData = \[.*?\];',
            f'const rawData = {data_json};',
            content,
            flags=re.DOTALL
        )
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Updated {html_path}')
    else:
        print(f'{html_path} not found — skipping HTML update')

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

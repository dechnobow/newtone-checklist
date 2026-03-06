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

    for title_el in soup.select('a[href*="/product/"]'):
        try:
            href = title_el.get('href', '')
            if not href.startswith('http'):
                href = BASE_URL + '/' + href.lstrip('/')

            m = re.search(r'/product/(\d+)', href)
            if not m:
                continue
            rid = m.group(1)

            item = title_el.parent
            for _ in range(4):
                if item is None:
                    break
                if item.name == 'li':
                    break
                item = item.parent
            if item is None:
                item = title_el.parent

            img_el = item.select_one('img') if item else None
            img = ''
            if img_el:
                img = img_el.get('src', '')
                if img and not img.startswith('http'):
                    img = BASE_URL + img

            artist_el = item.select_one('h3') if item else None
            artist = artist_el.get_text(strip=True) if artist_el else ''

            label_els = item.select('a') if item else []
            label = ''
            for a in label_els:
                ahref = a.get('href', '')
                if '/label/' in ahref and a != title_el:
                    label = a.get_text(strip=True)
                    break

            title = title_el.get_text(strip=True)
            if not title:
                continue

            fmt = ''
            if item:
                for child in item.children:
                    text = child.get_text(strip=True) if hasattr(child, 'get_text') else str(child).strip()
                    if text and len(text) < 30 and text not in (title, artist, label):
                        fmt = text
                        break

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
    print(f'  Found {len(unique)} items')
    return unique

def scrape_all():
    categories = {
        'new': '/store/',
    }
    all_groups = []
    for cat, path in categories.items():
        url = BASE_URL + path
        print(f'Scraping {cat}...')
        soup = fetch(url)
        records = parse_products(soup, cat)
        if records:
            all_groups.append({
                'date': today,
                'category': cat,
                'records': records
            })
    return all_groups

def load_existing_data():
    data_path = 'data.json'
    if not os.path.exists(data_path):
        print('data.json not found, starting fresh')
        return []
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f'Loaded {len(data)} existing groups from data.json')
        return data
    except Exception as e:
        print(f'Failed to load data.json: {e}')
        return []

def merge_data(existing, new_groups):
    existing_ids = set()
    for g in existing:
        for r in g.get('records', []):
            existing_ids.add(r.get('id'))

    deduped_groups = []
    for g in new_groups:
        new_records = [r for r in g.get('records', []) if r.get('id') not in existing_ids]
        print(f"  {g['category']}: {len(g['records'])} items -> {len(new_records)} new items after dedup")
        if new_records:
            deduped_groups.append({**g, 'records': new_records})

    merged = [g for g in existing if g.get('date') != today]
    merged.extend(deduped_groups)
    merged.sort(key=lambda g: (g.get('date', ''), g.get('category', '')), reverse=True)
    cutoff = (datetime.now(JST) - timedelta(days=90)).strftime('%Y-%m-%d')
    merged = [g for g in merged if g.get('date', '') >= cutoff]
    print(f'Total groups after merge: {len(merged)}')
    return merged

def save_data(data):
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('Saved data.json successfully')

if __name__ == '__main__':
    print(f'Date: {today}')
    new_groups = scrape_all()
    if new_groups:
        existing = load_existing_data()
        merged = merge_data(existing, new_groups)
        save_data(merged)
        print('Done.')
    else:
        print('No data scraped — skipping update')

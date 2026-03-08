import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import json
import re
import os
import sys
import time

JST = timezone(timedelta(hours=9))
today = datetime.now(JST).strftime('%Y-%m-%d')

BASE_URL = 'https://newtone-records.com'
STORE_URL = 'https://newtone-records.com/store/'
GRID_LIST_URL = 'https://newtone-records.com/include/grid_list.php'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'ja,en-US;q=0.9',
    'Referer': STORE_URL,
    'Origin': BASE_URL,
}


def create_session():
    """セッションを作成し、/store/にアクセスしてPHPSESSIDを取得する"""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        print('Initializing session...')
        r = session.get(STORE_URL, timeout=20)
        r.raise_for_status()
        print(f'  Session initialized. Cookies: {dict(session.cookies)}')
        return session, r.text
    except Exception as e:
        print(f'  Error initializing session: {e}')
        return None, None


def fetch_page_via_grid_list(session, page_index, total):
    payload = {
        "path": "/store/",
        "page": "",
        "total": total,
        "pp": 20,
        "dbl": 0,
        "products": page_index,
        "mode": "view-more",
        "active": "sort-list",
        "showmode": "",
        "pagesrc": ""
    }
    try:
        r = session.post(GRID_LIST_URL, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data
    except Exception as e:
        print(f'  Error fetching page {page_index}: {e}')
        return None


def parse_articles(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    records_by_date = {}

    for article in soup.select('article.list-single'):
        try:
            article_id = article.get('id', '')
            m = re.search(r'n_t(\d+)hr', article_id)
            if not m:
                continue
            rid = m.group(1)

            date_el = article.select_one('li.updated')
            date = date_el.get_text(strip=True) if date_el else ''
            if not date:
                date = today

            artist_el = article.select_one('h1.item_title strong')
            artist = artist_el.get_text(strip=True) if artist_el else ''

            title_el = article.select_one('h1.item_title a[href*="/product/"]')
            title = title_el.get_text(strip=True) if title_el else ''
            if not title:
                continue

            label_el = article.select_one('a.btn-label')
            label = label_el.get_text(strip=True) if label_el else ''

            img_el = article.select_one('img')
            img = img_el.get('src', '') if img_el else ''
            if img and not img.startswith('http'):
                img = BASE_URL + img

            fmt_el = article.select_one('ul.tab-list li')
            fmt = fmt_el.get('tab', '') if fmt_el else ''

            url = f'{BASE_URL}/product/{rid}'

            instock_el = article.select_one('.instock')
            outstock_el = article.select_one('.outofstock')
            in_stock = instock_el is not None and outstock_el is None

            record = {
                'id':      rid,
                'artist':  artist,
                'title':   title,
                'label':   label,
                'format':  fmt,
                'genre':   '',
                'img':     img,
                'url':     url,
                'used':    False,
                'preorder': False,
                'inStock': in_stock,
            }

            if date not in records_by_date:
                records_by_date[date] = []
            records_by_date[date].append(record)

        except Exception as e:
            print(f'  Parse error: {e}')

    return records_by_date


def scrape_all_pages():
    session, first_html = create_session()
    if not session:
        return {}

    # page 1はセッション初期化時のHTMLから取得
    all_records_by_date = {}
    page1_records = parse_articles(first_html)
    for date, recs in page1_records.items():
        if date not in all_records_by_date:
            all_records_by_date[date] = []
        all_records_by_date[date].extend(recs)
    count1 = sum(len(v) for v in page1_records.values())
    print(f'  Page 1: {count1} items, dates: {sorted(page1_records.keys())}')

    # totalを取得するためにgrid_list.phpを1回叩く
    first_api = fetch_page_via_grid_list(session, 1, 500)
    if not first_api:
        return all_records_by_date

    total = first_api.get('total', 0)
    pp = 20
    total_pages = (total + pp - 1) // pp
    print(f'Total items: {total}, pages: {total_pages}')

    for page_idx in range(2, total_pages + 1):
        print(f'  Fetching page {page_idx}/{total_pages}...')
        time.sleep(0.5)
        data = fetch_page_via_grid_list(session, page_idx, total)
        if not data:
            continue
        content = data.get('content', '')
        if not content:
            continue
        page_records = parse_articles(content)
        for date, recs in page_records.items():
            if date not in all_records_by_date:
                all_records_by_date[date] = []
            all_records_by_date[date].extend(recs)
        count = sum(len(v) for v in page_records.values())
        print(f'  Page {page_idx}: {count} items, dates: {sorted(page_records.keys())}')

    return all_records_by_date


def scrape_today_only():
    print('Fetching today only (page 1)...')
    session, first_html = create_session()
    if not session:
        return {}

    records_by_date = parse_articles(first_html)
    print(f'  Page 1: {sum(len(v) for v in records_by_date.values())} items')
    return records_by_date


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


def merge_with_existing(existing, new_records_by_date):
    existing_ids = set()
    for g in existing:
        for r in g.get('records', []):
            existing_ids.add(r.get('id'))

    existing_by_date = {}
    for g in existing:
        d = g.get('date', '')
        existing_by_date[d] = g

    for date, new_recs in new_records_by_date.items():
        truly_new = [r for r in new_recs if r.get('id') not in existing_ids]
        if not truly_new:
            print(f'  {date}: 0 new items (all already exist)')
            continue
        print(f'  {date}: {len(truly_new)} new items')
        if date in existing_by_date:
            existing_by_date[date]['records'].extend(truly_new)
        else:
            existing_by_date[date] = {
                'date': date,
                'category': 'new',
                'records': truly_new,
            }
        for r in truly_new:
            existing_ids.add(r.get('id'))

    merged = list(existing_by_date.values())
    merged.sort(key=lambda g: g.get('date', ''), reverse=True)
    cutoff = (datetime.now(JST) - timedelta(days=90)).strftime('%Y-%m-%d')
    merged = [g for g in merged if g.get('date', '') >= cutoff]
    print(f'Total groups after merge: {len(merged)}')
    return merged


def save_data(data):
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('Saved data.json successfully')


if __name__ == '__main__':
    full_scrape = '--full' in sys.argv
    date_from = None
    date_to = None
    if '--from' in sys.argv:
        idx = sys.argv.index('--from')
        date_from = sys.argv[idx + 1]
    if '--to' in sys.argv:
        idx = sys.argv.index('--to')
        date_to = sys.argv[idx + 1]

    print(f'Date: {today}')

    if date_from and date_to:
        print(f'=== RANGE SCRAPE MODE: {date_from} ~ {date_to} ===')
        records_by_date = scrape_all_pages()
        records_by_date = {
            d: recs for d, recs in records_by_date.items()
            if date_from <= d <= date_to
        }
        print(f'After filtering: {len(records_by_date)} dates in range')
    elif full_scrape:
        print('=== FULL SCRAPE MODE (all pages) ===')
        records_by_date = scrape_all_pages()
    else:
        print('=== DAILY MODE (today only) ===')
        records_by_date = scrape_today_only()

    if not records_by_date:
        print('No data scraped — skipping update')
    else:
        total = sum(len(v) for v in records_by_date.values())
        print(f'Scraped {total} items across {len(records_by_date)} dates')
        existing = load_existing_data()
        merged = merge_with_existing(existing, records_by_date)
        save_data(merged)
        print('Done.')

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

            # 親要素をさかのぼって情報を取得
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

            # フォーマット（li直下のテキストノードやspanから）
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

    # 重複除去（同じカテゴリ内のみ）
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

    # JSON形式で試す
    try:
        data = json.loads(raw)
        print(f'Loaded {len(data)} existing groups (JSON)')
        return data
    except:
        pass

    # JS形式をJSONに変換
    try:
        # キー名にダブルクォートを追加
        json_str = re.sub(r'(?<=[{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', raw)
        # シングルクォートをダブルクォートに
        json_str = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", lambda mo: json.dumps(mo.group(1)), json_str)
        # 末尾カンマを除去
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        data = json.loads(json_str)
        print(f'Loaded {len(data)} existing groups (JS converted)')
        return data
    except Exception as e:
        print(f'Failed to parse existing data: {e}')
        return []

def merge_data(existing, new_groups):
    # 既存データ全体のIDセットを作成
    existing_ids = set()
    for g in existing:
        for r in g.get('records', []):
            existing_ids.add(r.get('id'))

    # 今日のデータから既存IDと重複するものを除去
    deduped_groups = []
    for g in new_groups:
        new_records = [r for r in g.get('records', []) if r.get('id') not in existing_ids]
        print(f"  {g['category']}: {len(g['records'])} items -> {len(new_records)} new items after dedup")
        if new_records:
            deduped_groups.append({**g, 'records': new_records})

    # 今日のデータを除いた既存データに新データを追加
    merged = [g for g in existing if g.get('date') != today]
    merged.extend(deduped_groups)
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

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

JST = timezone(timedelta(hours=9))
BASE_URL = "https://newtone-records.com"
STORE_URL = "https://newtone-records.com/store/"


def today_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def normalize_date(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else ""


def absolute_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return BASE_URL + url
    return BASE_URL + "/" + url


def parse_article(article):
    article_id = article.get("id", "") or ""

    rid = ""
    m = re.search(r"n_t(\d+)", article_id)
    if m:
        rid = m.group(1)

    title = ""
    url = ""

    title_link = (
        article.select_one('h1.item_title a[href*="/product/"]')
        or article.select_one('a[href*="/product/"]')
    )
    if title_link:
        title = title_link.get_text(" ", strip=True)
        href = title_link.get("href", "")
        url = absolute_url(href)
        if not rid:
            m = re.search(r"/product/(\d+)", href)
            if m:
                rid = m.group(1)

    if not rid or not title:
        return None

    artist = ""
    artist_el = article.select_one("h1.item_title strong")
    if artist_el:
        artist = artist_el.get_text(" ", strip=True)

    label = ""
    label_el = article.select_one("a.btn-label")
    if label_el:
        label = label_el.get_text(" ", strip=True)

    date = ""
    date_el = article.select_one("li.updated")
    if date_el:
        date = normalize_date(date_el.get_text(" ", strip=True))
    if not date:
        date = normalize_date(article.get_text("\n", strip=True))
    if not date:
        return None

    img = ""
    img_el = article.select_one("img")
    if img_el and img_el.get("src"):
        img = absolute_url(img_el.get("src"))

    fmt = ""
    fmt_el = article.select_one("ul.tab-list li[tab]")
    if fmt_el:
        fmt = (fmt_el.get("tab") or "").strip()
    if not fmt:
        text = article.get_text(" ", strip=True)
        m = re.search(r"\b(12inch|LP|EP|CD|Cassette|Digital|2LP|3LP|LP\+DL|CD＋DL)\b", text, re.I)
        if m:
            fmt = m.group(1)

    in_stock = article.select_one(".instock") is not None and article.select_one(".outofstock") is None

    return {
        "id": rid,
        "artist": artist,
        "title": title,
        "label": label,
        "format": fmt,
        "genre": "",
        "img": img,
        "url": url or f"{BASE_URL}/product/{rid}",
        "used": False,
        "preorder": False,
        "inStock": in_stock,
        "_date": date,
    }


def parse_records_from_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.list-single") or soup.select("article[id^='n_t']")

    grouped = defaultdict(list)
    seen_ids = set()

    for article in articles:
        rec = parse_article(article)
        if not rec:
            continue

        rid = rec["id"]
        if rid in seen_ids:
            continue
        seen_ids.add(rid)

        date = rec.pop("_date")
        grouped[date].append(rec)

    return dict(grouped)


def extract_dates_in_dom_order(html: str):
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.list-single") or soup.select("article[id^='n_t']")

    dates = []
    for article in articles:
        rec = parse_article(article)
        if rec and rec.get("_date"):
            dates.append(rec["_date"])
    return dates


def get_frontier_oldest_date(html: str):
    dates = extract_dates_in_dom_order(html)
    if not dates:
        return None

    first_date = dates[0]
    frontier = first_date
    prev = datetime.strptime(first_date, "%Y-%m-%d").date()

    for d in dates:
        cur = datetime.strptime(d, "%Y-%m-%d").date()

        if cur > prev:
            break

        if (prev - cur).days > 30:
            break

        frontier = d
        prev = cur

    return frontier


def click_view_more_until(page, target_from: str, max_clicks: int = 80):
    target_from_date = datetime.strptime(target_from, "%Y-%m-%d").date()

    for i in range(max_clicks):
        html_before = page.content()
        frontier = get_frontier_oldest_date(html_before)

        if frontier:
            frontier_date = datetime.strptime(frontier, "%Y-%m-%d").date()
            print(f"[{i}] frontier oldest date: {frontier}")

            if frontier_date <= target_from_date:
                print("Target start date reached.")
                break

        button = page.locator("text=View More")
        if button.count() == 0:
            print("View More button not found. Stop.")
            break

        before_html_len = len(html_before)

        try:
            button.first.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(500)
            button.first.click(timeout=5000)
        except Exception as e:
            print(f"Failed to click View More: {e}")
            break

        page.wait_for_timeout(2000)

        html_after = page.content()
        after_html_len = len(html_after)

        if after_html_len <= before_html_len:
            print("No further content increase after click. Stop.")
            break


def scrape_range(date_from: str, date_to: str):
    print(f"Range scrape: {date_from} ~ {date_to}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 2200})

        page.goto(STORE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        click_view_more_until(page, date_from)

        final_html = page.content()
        browser.close()

    grouped = parse_records_from_html(final_html)

    filtered = []
    for d in sorted(grouped.keys(), reverse=True):
        if date_from <= d <= date_to:
            filtered.append({
                "date": d,
                "category": "range",
                "records": grouped[d]
            })

    print(f"Filtered groups: {len(filtered)}")
    total = sum(len(g["records"]) for g in filtered)
    print(f"Filtered records: {total}")
    return filtered


def scrape_today_only():
    today = today_jst_str()
    result = scrape_range(today, today)

    # 通常データとして category を new に寄せる
    normalized = []
    for g in result:
        normalized.append({
            "date": g["date"],
            "category": "new",
            "records": g["records"]
        })
    return normalized


def load_existing_data(path="data.json"):
    if not os.path.exists(path):
        print(f"{path} not found, starting fresh")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"Loaded {len(data)} existing groups from {path}")
        return data
    except Exception as e:
        print(f"Failed to load {path}: {e}")
        return []


def merge_groups(existing, incoming):
    existing_ids = set()
    by_date = {}

    for g in existing:
        by_date[g["date"]] = g
        for r in g.get("records", []):
            if r.get("id"):
                existing_ids.add(r["id"])

    for g in incoming:
        date = g["date"]
        new_records = [r for r in g["records"] if r.get("id") not in existing_ids]

        if not new_records:
            continue

        if date in by_date:
            by_date[date]["records"].extend(new_records)
        else:
            by_date[date] = {
                "date": date,
                "category": g.get("category", "new"),
                "records": new_records
            }

        for r in new_records:
            if r.get("id"):
                existing_ids.add(r["id"])

    merged = list(by_date.values())
    merged.sort(key=lambda x: x["date"], reverse=True)

    cutoff = (datetime.now(JST) - timedelta(days=90)).strftime("%Y-%m-%d")
    merged = [g for g in merged if g.get("date", "") >= cutoff]

    print(f"Total groups after merge: {len(merged)}")
    return merged


def save_data(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved {path} successfully")


if __name__ == "__main__":
    date_from = None
    date_to = None
    full_scrape = "--full" in sys.argv

    if "--from" in sys.argv:
        i = sys.argv.index("--from")
        if i + 1 < len(sys.argv):
            date_from = sys.argv[i + 1]

    if "--to" in sys.argv:
        i = sys.argv.index("--to")
        if i + 1 < len(sys.argv):
            date_to = sys.argv[i + 1]

    print(f"Today (JST): {today_jst_str()}")

    # 範囲取得は tmp-range.json に保存
    if date_from and date_to:
        result = scrape_range(date_from, date_to)
        save_data(result, "tmp-range.json")
        print("Done.")
        sys.exit(0)

    # full scrape は data.json 再構築扱い
    if full_scrape:
        result = scrape_range("2000-01-01", today_jst_str())
        normalized = []
        for g in result:
            normalized.append({
                "date": g["date"],
                "category": "new",
                "records": g["records"]
            })
        save_data(normalized, "data.json")
        print("Done.")
        sys.exit(0)

    # 通常の日次更新
    today_groups = scrape_today_only()
    if not today_groups:
        print("No data scraped — skipping update")
        sys.exit(0)

    existing = load_existing_data("data.json")
    merged = merge_groups(existing, today_groups)
    save_data(merged, "data.json")
    print("Done.")

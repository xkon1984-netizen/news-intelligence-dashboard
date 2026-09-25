import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
UA = "Mozilla/5.0 NewsIntelligencePublisherWatcher/1.0"


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def strip_html(value):
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def collect_wordpress(pub):
    out = []
    params = {
        "per_page": min(int(pub.get("max_items", 40)), 100),
        "_fields": "id,date_gmt,modified_gmt,link,slug,title,excerpt,author,categories,tags"
    }
    try:
        r = requests.get(pub["wp_api"], params=params, timeout=25, headers={"User-Agent": UA})
        r.raise_for_status()
        posts = r.json()
    except Exception as exc:
        print(f"WordPress fetch failed for {pub['name']}: {exc}")
        return out
    for post in posts:
        title = strip_html((post.get("title") or {}).get("rendered", ""))
        excerpt = strip_html((post.get("excerpt") or {}).get("rendered", ""))
        if not title or not post.get("link"):
            continue
        out.append({
            "publisher": pub["name"],
            "mode": pub["mode"],
            "publisher_type": "wordpress",
            "cms_id": str(post.get("id", "")),
            "title": title,
            "excerpt": excerpt,
            "url": post.get("link", ""),
            "slug": post.get("slug", ""),
            "published": post.get("date_gmt", ""),
            "modified": post.get("modified_gmt", ""),
            "author_id": str(post.get("author", "")),
            "categories": post.get("categories", []),
            "tags": post.get("tags", []),
        })
    return out


def collect_listing(pub):
    out = []
    try:
        r = requests.get(pub["listing_url"], timeout=25, headers={"User-Agent": UA})
        r.raise_for_status()
    except Exception as exc:
        print(f"Listing fetch failed for {pub['name']}: {exc}")
        return out
    soup = BeautifulSoup(r.text, "html.parser")
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        text = " ".join(a.stripped_strings).strip()
        if not href or not text or len(text) < 12:
            continue
        url = urljoin(pub["base_url"], href)
        if pub["base_url"].replace("https://", "").replace("http://", "") not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        # Keep likely article URLs, not nav/category links.
        if "/page/" in url or url.rstrip("/") in {
            pub["base_url"].rstrip("/"),
            pub["listing_url"].rstrip("/")
        }:
            continue
        out.append({
            "publisher": pub["name"],
            "mode": pub["mode"],
            "publisher_type": "listing",
            "cms_id": "",
            "title": text,
            "excerpt": "",
            "url": url,
            "slug": url.rstrip("/").split("/")[-1],
            "published": "",
            "modified": "",
            "author_id": "",
            "categories": [],
            "tags": [],
        })
        if len(out) >= int(pub.get("max_items", 60)):
            break
    return out


def main():
    cfg = load_yaml(ROOT / "config" / "publishers.yaml")
    items = []
    for pub in cfg.get("publishers", []):
        if pub.get("type") == "wordpress":
            items.extend(collect_wordpress(pub))
        elif pub.get("type") == "listing":
            items.extend(collect_listing(pub))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "published_watch.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"Collected {len(items)} published items")


if __name__ == "__main__":
    main()

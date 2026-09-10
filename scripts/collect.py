import json
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, quote_plus

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
USER_AGENT = "Mozilla/5.0 NewsIntelligenceDashboard/1.0"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def merge_rules(base: dict, extra: dict) -> dict:
    merged = {"high": {}, "medium": {}}
    for group in ("high", "medium"):
        merged[group].update(base.get(group, {}))
        merged[group].update(extra.get(group, {}))
    return merged


def keyword_matches(text: str, keyword: str) -> bool:
    if keyword.isupper() and keyword.isalnum() and len(keyword) <= 5:
        pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
        return re.search(pattern, text, flags=re.IGNORECASE | re.UNICODE) is not None
    return keyword.casefold() in text.casefold()


def score_text(text: str, rules: dict):
    score = 0
    matched = []
    for group in ("high", "medium"):
        for keyword, points in rules.get(group, {}).items():
            if keyword_matches(text, keyword):
                score += int(points)
                matched.append(keyword)
    return min(score, 100), matched


def parse_published(value: str):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None


def collect_source(source: dict, keywords: dict, modes: dict):
    results = []
    feed = feedparser.parse(source["url"])
    now = datetime.now(timezone.utc)
    for entry in feed.entries[:50]:
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        summary = (entry.get("summary") or "").strip()
        published_raw = entry.get("published") or entry.get("updated") or ""
        if not title or not url:
            continue
        published_dt = parse_published(published_raw)
        max_age_days = source.get("max_age_days")
        if max_age_days is None and source.get("modes") == ["sportdog"]:
            max_age_days = 10
        if max_age_days is not None and published_dt is not None and published_dt < now - timedelta(days=int(max_age_days)):
            continue
        text = f"{title} {summary}"
        required_any = source.get("required_any", [])
        if required_any and not any(keyword_matches(text, kw) for kw in required_any):
            continue
        scores, matches = {}, {}
        for mode in source.get("modes", []):
            raw_score, matched = score_text(text, keywords.get(mode, {}))
            weighted = min(100, round(raw_score * float(source.get("source_weight", 1))))
            threshold = int(modes.get(mode, {}).get("threshold", 0))
            if weighted >= threshold:
                scores[mode] = weighted
                matches[mode] = matched
        if scores:
            results.append({"title": title, "url": url, "source": source["name"], "published": published_raw, "published_ts": published_dt.timestamp() if published_dt else 0, "scores": scores, "matched_keywords": matches})
    return results


def collect_frontpage_source(source: dict, keywords: dict, modes: dict):
    results = []
    try:
        response = requests.get(source["url"], timeout=20, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except Exception as exc:
        print(f"Front page fetch failed for {source['name']}: {exc}")
        return results
    soup = BeautifulSoup(response.text, "html.parser")
    now = datetime.now(timezone.utc)
    published_raw = now.isoformat()
    required_any = source.get("required_any", [])
    seen = set()
    for node in soup.select("a, h1, h2, h3, h4, figcaption"):
        text = " ".join(node.stripped_strings).strip()
        if len(text) < 8 or len(text) > 300:
            continue
        normalized = re.sub(r"\s+", " ", text.casefold())
        if normalized in seen:
            continue
        seen.add(normalized)
        if required_any and not any(keyword_matches(text, kw) for kw in required_any):
            continue
        scores, matches = {}, {}
        for mode in source.get("modes", []):
            raw_score, matched = score_text(text, keywords.get(mode, {}))
            weighted = min(100, round(raw_score * float(source.get("source_weight", 1))))
            threshold = int(modes.get(mode, {}).get("threshold", 0))
            if weighted >= threshold:
                scores[mode] = weighted
                matches[mode] = matched
        if not scores:
            continue
        href = node.get("href") if node.name == "a" else None
        item_url = urljoin(source["url"], href) if href else source["url"]
        results.append({"title": f"[ΠΡΩΤΟΣΕΛΙΔΟ] {text}", "url": item_url, "source": source["name"], "published": published_raw, "published_ts": now.timestamp(), "scores": scores, "matched_keywords": matches, "content_type": "frontpage"})
    return results[:40]


def locale_params(locale: str):
    if locale == "tr-TR":
        return "tr", "TR", "TR:tr"
    if locale == "el-GR":
        return "el", "GR", "GR:el"
    return "en", "US", "US:en"


def chunked(values, size):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def analyst_groups(analysts):
    grouped = {}
    for analyst in analysts:
        grouped.setdefault(analyst.get("locale", "en-US"), []).append(analyst)
    for locale, group in grouped.items():
        for subset in chunked(group, 5):
            yield locale, subset


def match_analyst(text: str, analysts):
    for analyst in analysts:
        for alias in analyst.get("aliases", [analyst["name"]]):
            if keyword_matches(text, alias):
                return analyst, alias
    return None, None


def collect_analyst_news(analysts):
    """Find analyst appearances in websites, TV/radio sites and indexed video pages."""
    results = []
    now = datetime.now(timezone.utc)
    for locale, group in analyst_groups(analysts):
        hl, gl, ceid = locale_params(locale)
        query = " OR ".join(f'\"{a["name"]}\"' for a in group) + " when:3d"
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={quote_plus(ceid)}"
        feed = feedparser.parse(url)
        for entry in feed.entries[:100]:
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or "").strip()
            link = (entry.get("link") or "").strip()
            published_raw = entry.get("published") or entry.get("updated") or ""
            published_dt = parse_published(published_raw)
            if not title or not link:
                continue
            if published_dt and published_dt < now - timedelta(days=4):
                continue
            analyst, alias = match_analyst(f"{title} {summary}", group)
            if not analyst:
                continue
            results.append({
                "title": f"[ANALYST WATCH] {analyst['name']} — {title}",
                "url": link,
                "source": f"Analysts Watch · {analyst['country']}",
                "published": published_raw,
                "published_ts": published_dt.timestamp() if published_dt else 0,
                "scores": {"geopolitico": 75},
                "matched_keywords": {"geopolitico": [analyst["name"], alias]},
                "content_type": "analyst",
                "analyst": analyst["name"],
                "analyst_country": analyst["country"],
            })
    return results


def extract_yt_initial_data(html: str):
    markers = ["var ytInitialData = ", "ytInitialData = "]
    for marker in markers:
        start = html.find(marker)
        if start == -1:
            continue
        start += len(marker)
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(html[start:])
            return data
        except Exception:
            pass
    return None


def walk_video_renderers(node):
    if isinstance(node, dict):
        if "videoRenderer" in node and isinstance(node["videoRenderer"], dict):
            yield node["videoRenderer"]
        for value in node.values():
            yield from walk_video_renderers(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_video_renderers(value)


def text_from_runs(value):
    if not isinstance(value, dict):
        return ""
    if "simpleText" in value:
        return value.get("simpleText", "")
    return "".join(run.get("text", "") for run in value.get("runs", []) if isinstance(run, dict))


def relative_time_to_datetime(value: str, now):
    text = value.casefold()
    patterns = [
        (r"(\d+)\s+minute", "minutes"),
        (r"(\d+)\s+hour", "hours"),
        (r"(\d+)\s+day", "days"),
        (r"(\d+)\s+week", "weeks"),
    ]
    for pattern, unit in patterns:
        match = re.search(pattern, text)
        if match:
            return now - timedelta(**{unit: int(match.group(1))})
    if "yesterday" in text:
        return now - timedelta(days=1)
    return now


def collect_analyst_youtube(analysts):
    """Search recent public YouTube results without using the paid YouTube API."""
    results = []
    now = datetime.now(timezone.utc)
    for analyst in analysts:
        query = quote_plus(analyst["name"])
        url = f"https://www.youtube.com/results?search_query={query}&sp=CAI%253D&hl=en"
        try:
            response = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
            response.raise_for_status()
            data = extract_yt_initial_data(response.text)
            if not data:
                continue
        except Exception as exc:
            print(f"YouTube analyst search failed for {analyst['name']}: {exc}")
            continue
        added = 0
        for video in walk_video_renderers(data):
            video_id = video.get("videoId")
            title = text_from_runs(video.get("title"))
            published_text = text_from_runs(video.get("publishedTimeText"))
            owner = text_from_runs(video.get("ownerText"))
            if not video_id or not title:
                continue
            full_text = f"{title} {owner}"
            if not any(keyword_matches(full_text, alias) for alias in analyst.get("aliases", [analyst["name"]])):
                continue
            published_dt = relative_time_to_datetime(published_text, now)
            if published_dt < now - timedelta(days=4):
                continue
            results.append({
                "title": f"[ANALYST WATCH · YOUTUBE] {analyst['name']} — {title}",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "source": f"YouTube · {owner or analyst['name']}",
                "published": published_dt.isoformat(),
                "published_ts": published_dt.timestamp(),
                "scores": {"geopolitico": 80},
                "matched_keywords": {"geopolitico": [analyst["name"], "YouTube"]},
                "content_type": "analyst",
                "analyst": analyst["name"],
                "analyst_country": analyst["country"],
            })
            added += 1
            if added >= 5:
                break
    return results


def dedupe_items(items):
    seen = set()
    output = []
    for item in items:
        key = item.get("url") or item.get("title")
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def main():
    source_cfg = load_yaml(ROOT / "config" / "sources.yaml") or {"sources": []}
    for extra_sources_name in (
        "pontos_greek_sources.yaml",
        "sportdog_sources.yaml",
        "geopolitico_turkey_sources.yaml",
        "geopolitico_greek_sources.yaml",
        "geopolitico_analysis_sources.yaml",
    ):
        extra_sources_path = ROOT / "config" / extra_sources_name
        if extra_sources_path.exists():
            extra_sources = load_yaml(extra_sources_path) or {}
            source_cfg.setdefault("sources", []).extend(extra_sources.get("sources", []))

    keywords = load_yaml(ROOT / "config" / "keywords.yaml")
    for extra_name in ("pontos_turkey_keywords.yaml", "pontos_greek_keywords.yaml"):
        extra_path = ROOT / "config" / extra_name
        if extra_path.exists():
            keywords["pontos_voice"] = merge_rules(keywords.get("pontos_voice", {}), load_yaml(extra_path) or {})
    sportdog_extra_path = ROOT / "config" / "sportdog_extra_keywords.yaml"
    if sportdog_extra_path.exists():
        keywords["sportdog"] = merge_rules(keywords.get("sportdog", {}), load_yaml(sportdog_extra_path) or {})
    geopolitico_turkey_keywords = ROOT / "config" / "geopolitico_turkey_keywords.yaml"
    if geopolitico_turkey_keywords.exists():
        keywords["geopolitico"] = merge_rules(keywords.get("geopolitico", {}), load_yaml(geopolitico_turkey_keywords) or {})

    modes = load_yaml(ROOT / "config" / "modes.yaml").get("modes", {})
    items = []
    for source in source_cfg.get("sources", []):
        items.extend(collect_source(source, keywords, modes))

    frontpage_path = ROOT / "config" / "frontpage_sources.yaml"
    if frontpage_path.exists():
        for source in (load_yaml(frontpage_path) or {}).get("frontpages", []):
            items.extend(collect_frontpage_source(source, keywords, modes))

    analysts_path = ROOT / "config" / "analysts.yaml"
    if analysts_path.exists():
        analysts = (load_yaml(analysts_path) or {}).get("analysts", [])
        items.extend(collect_analyst_news(analysts))
        items.extend(collect_analyst_youtube(analysts))

    items = dedupe_items(items)
    items.sort(key=lambda item: (item.get("published_ts", 0), max(item["scores"].values())), reverse=True)
    for item in items:
        item.pop("published_ts", None)

    DATA_DIR.mkdir(exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "count": len(items), "items": items[:750]}
    (DATA_DIR / "news.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Collected {len(items)} relevant stories")


if __name__ == "__main__":
    main()

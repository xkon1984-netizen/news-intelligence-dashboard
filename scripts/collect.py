import json
import re
from difflib import SequenceMatcher
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
SOURCE_HEALTH = []



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



def clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)).strip()


def contains_greek(value: str) -> bool:
    return bool(re.search(r"[\u0370-\u03ff\u1f00-\u1fff]", value or ""))


def fetch_article_text(url: str):
    """Best-effort public-page read. Never bypasses paywalls or access controls."""
    if not url:
        return "", url, "no_url"
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8,el;q=0.7"}, allow_redirects=True)
        response.raise_for_status()
        final_url = response.url
        if "news.google.com" in final_url:
            return "", final_url, "summary_only"
        ctype = response.headers.get("content-type", "")
        if "html" not in ctype:
            return "", final_url, "non_html"
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "nav", "footer", "header", "aside"]):
            node.decompose()
        article = soup.find("article") or soup.find("main") or soup.body
        text = re.sub(r"\s+", " ", article.get_text(" ", strip=True) if article else "").strip()
        if len(text) < 250:
            return text, final_url, "partial"
        return text[:30000], final_url, "full_text"
    except Exception:
        return "", url, "fetch_failed"


def mention_context_excerpt(text: str, aliases):
    folded = text.casefold()
    for alias in aliases:
        pos = folded.find(alias.casefold())
        if pos >= 0:
            start = max(0, pos - 120)
            end = min(len(text), pos + len(alias) + 160)
            return text[start:end].strip()
    return ""


def classify_mention(text: str, cfg: dict):
    aliases = cfg.get("aliases", [])
    authored = cfg.get("authored_markers", [])
    identity = cfg.get("identity_context", [])
    alias_hits = [a for a in aliases if keyword_matches(text, a)]
    if not alias_hits:
        if keyword_matches(text, "Geopolitico") or keyword_matches(text, "Geopolitico.gr"):
            return "geopolitico_reference", []
        return None, []
    if any(keyword_matches(text, marker) for marker in authored):
        return "authored_byline", alias_hits
    if any(keyword_matches(text, marker) for marker in identity):
        return "personal_reference", alias_hits
    # Exact name without corroborating professional context is retained as a candidate,
    # not promoted as a confirmed personal reference.
    return "name_candidate", alias_hits


def locale_params_extended(locale: str):
    if locale == "tr-TR":
        return "tr", "TR", "TR:tr"
    if locale == "el-GR":
        return "el", "GR", "GR:el"
    return "en", "US", "US:en"


def load_first_seen():
    path = DATA_DIR / "news.json"
    if not path.exists():
        return {}
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        return {item.get("url"): item.get("first_detected_at") or old.get("generated_at") for item in old.get("items", []) if item.get("url")}
    except Exception:
        return {}


def enrich_item(item: dict, first_seen: dict, now):
    item["first_detected_at"] = first_seen.get(item.get("url")) or now.isoformat()
    if "title_el" not in item:
        item["title_el"] = item.get("title", "") if contains_greek(item.get("title", "")) else ""
    if "summary_el" not in item:
        summary = item.get("summary", "")
        item["summary_el"] = summary if contains_greek(summary) else ""
    return item


def collect_mentions(cfg: dict):
    results = []
    now = datetime.now(timezone.utc)
    for search in cfg.get("searches", []):
        hl, gl, ceid = locale_params_extended(search.get("locale", "en-US"))
        url = f"https://news.google.com/rss/search?q={quote_plus(search['query'])}&hl={hl}&gl={gl}&ceid={quote_plus(ceid)}"
        checked = now.isoformat()
        try:
            feed = feedparser.parse(url)
            if getattr(feed, "bozo", False) and not feed.entries:
                raise RuntimeError(str(getattr(feed, "bozo_exception", "feed parse error")))
            accepted = 0
            for entry in feed.entries[:100]:
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or "").strip()
                summary = clean_html(entry.get("summary") or "")
                published_raw = entry.get("published") or entry.get("updated") or ""
                published_dt = parse_published(published_raw)
                if not title or not link:
                    continue
                if published_dt and published_dt < now - timedelta(days=31):
                    continue
                page_text, resolved_url, access = fetch_article_text(link)
                evidence_text = f"{title} {summary} {page_text}"
                mention_type, hits = classify_mention(evidence_text, cfg)
                if not mention_type:
                    continue
                confirmed = mention_type in ("personal_reference", "authored_byline")
                excerpt = mention_context_excerpt(evidence_text, hits)
                source_name = (entry.get("source") or {}).get("title") if isinstance(entry.get("source"), dict) else ""
                source_name = source_name or search["name"]
                item = {
                    "title": title,
                    "title_el": title if contains_greek(title) else "",
                    "url": resolved_url or link,
                    "source": source_name,
                    "published": published_raw,
                    "published_ts": published_dt.timestamp() if published_dt else 0,
                    "scores": {"geopolitico": 100 if confirmed else 65},
                    "matched_keywords": {"geopolitico": hits or ["Geopolitico"]},
                    "content_type": "mention",
                    "mention_type": mention_type,
                    "mention_confirmed": confirmed,
                    "mention_excerpt": excerpt,
                    "summary": summary,
                    "summary_el": summary if contains_greek(summary) else "",
                    "access_note": "Πλήρες δημόσιο κείμενο αναγνώστηκε." if access == "full_text" else "Διαθέσιμη μόνο περίληψη/αποσπασματική πρόσβαση.",
                    "journalistic_significance": "Επιβεβαιωμένη προσωπική αναφορά — προωθείται ανεξάρτητα από τη γενική βαθμολογία." if confirmed else "Αναφορά που χρειάζεται ταυτοποίηση συμφραζομένων.",
                    "retrospective": bool(published_dt and published_dt < now - timedelta(days=2)),
                }
                results.append(item)
                accepted += 1
            SOURCE_HEALTH.append({"source": search["name"], "category": "mentions", "checked_at": checked, "status": "ok", "entries": len(feed.entries), "accepted": accepted, "error": ""})
        except Exception as exc:
            SOURCE_HEALTH.append({"source": search["name"], "category": "mentions", "checked_at": checked, "status": "error", "entries": 0, "accepted": 0, "error": str(exc)[:300]})
    return results


def normalized_story_title(value: str) -> str:
    value = re.sub(r"\s+-\s+[^-]{2,40}$", "", value or "")
    value = re.sub(r"[^\w\s]", " ", value.casefold(), flags=re.UNICODE)
    stop = {"ve", "ile", "bir", "bu", "the", "a", "an", "of", "to", "in", "για", "και", "το", "η", "ο"}
    return " ".join(w for w in value.split() if len(w) > 2 and w not in stop)


def group_reproductions(items):
    """Conservatively group near-identical headlines while retaining every outlet."""
    output = []
    for item in items:
        if item.get("content_type") == "mention":
            output.append(item)
            continue
        norm = normalized_story_title(item.get("title", ""))
        matched = None
        for existing in output[-120:]:
            if existing.get("content_type") == "mention":
                continue
            other = normalized_story_title(existing.get("title", ""))
            if norm and other and SequenceMatcher(None, norm, other).ratio() >= 0.88:
                matched = existing
                break
        if not matched:
            item["related_sources"] = [{"source": item.get("source"), "url": item.get("url")}]
            output.append(item)
        else:
            refs = matched.setdefault("related_sources", [])
            if not any(r.get("source") == item.get("source") for r in refs):
                refs.append({"source": item.get("source"), "url": item.get("url")})
    return output


def collect_source(source: dict, keywords: dict, modes: dict):
    results = []
    now = datetime.now(timezone.utc)
    checked = now.isoformat()
    try:
        feed = feedparser.parse(source["url"])
        if getattr(feed, "bozo", False) and not feed.entries:
            raise RuntimeError(str(getattr(feed, "bozo_exception", "feed parse error")))
        for entry in feed.entries[:75]:
            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            summary = clean_html(entry.get("summary") or "")
            published_raw = entry.get("published") or entry.get("updated") or ""
            if not title or not url:
                continue
            published_dt = parse_published(published_raw)
            max_age_days = source.get("max_age_days")
            if max_age_days is None and source.get("modes") == ["sportdog"]:
                max_age_days = 10
            if max_age_days is not None and published_dt is not None and published_dt < now - timedelta(days=int(max_age_days)):
                continue

            page_text, resolved_url, access = ("", url, "summary_only")
            # Full-page reading is opt-in and best-effort; no paywall/access bypass.
            if source.get("fetch_full_text"):
                page_text, resolved_url, access = fetch_article_text(url)
            text = f"{title} {summary} {page_text}"

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
                item = {
                    "title": title, "title_el": title if contains_greek(title) else "",
                    "url": resolved_url or url, "source": source["name"], "published": published_raw,
                    "published_ts": published_dt.timestamp() if published_dt else 0,
                    "scores": scores, "matched_keywords": matches, "summary": summary,
                    "summary_el": summary if contains_greek(summary) else "",
                    "access_note": "Πλήρες δημόσιο κείμενο αναγνώστηκε." if access == "full_text" else "Διαθέσιμη μόνο περίληψη/αποσπασματική πρόσβαση.",
                }
                if source.get("content_type"):
                    item["content_type"] = source["content_type"]
                if source.get("content_type") == "turkey_greece":
                    item["journalistic_significance"] = "Τουρκικό δημοσίευμα σχετικό με Ελλάδα/ελληνοτουρκικά — κρατείται χωρίς να απαιτείται ελληνική αναπαραγωγή."
                results.append(item)
        SOURCE_HEALTH.append({"source": source["name"], "category": source.get("content_type", "news"), "checked_at": checked, "status": "ok", "entries": len(feed.entries), "accepted": len(results), "error": ""})
    except Exception as exc:
        SOURCE_HEALTH.append({"source": source.get("name", "unknown"), "category": source.get("content_type", "news"), "checked_at": checked, "status": "error", "entries": 0, "accepted": 0, "error": str(exc)[:300]})
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
            results.append({"title": f"[ANALYST WATCH] {analyst['name']} — {title}", "url": link, "source": f"Analysts Watch · {analyst['country']}", "published": published_raw, "published_ts": published_dt.timestamp() if published_dt else 0, "scores": {"geopolitico": 75}, "matched_keywords": {"geopolitico": [analyst["name"], alias]}, "content_type": "analyst", "analyst": analyst["name"], "analyst_country": analyst["country"]})
    return results


def extract_yt_initial_data(html: str):
    for marker in ["var ytInitialData = ", "ytInitialData = "]:
        start = html.find(marker)
        if start == -1:
            continue
        start += len(marker)
        try:
            data, _ = json.JSONDecoder().raw_decode(html[start:])
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
    """Parse YouTube relative upload age. Return None when age is unknown/old.

    Critical: never treat an unparsed age such as '3 months ago' as 'now'.
    """
    text = (value or "").casefold().strip()
    if not text:
        return None

    # English
    english = [
        (r"(\d+)\s+(?:second|seconds)\s+ago", "seconds"),
        (r"(\d+)\s+(?:minute|minutes)\s+ago", "minutes"),
        (r"(\d+)\s+(?:hour|hours)\s+ago", "hours"),
        (r"(\d+)\s+(?:day|days)\s+ago", "days"),
        (r"(\d+)\s+(?:week|weeks)\s+ago", "weeks"),
    ]
    # Greek variants commonly returned by YouTube.
    greek = [
        (r"πριν\s+από\s+(\d+)\s+δευτερ", "seconds"),
        (r"πριν\s+από\s+(\d+)\s+λεπτ", "minutes"),
        (r"πριν\s+από\s+(\d+)\s+ώρ", "hours"),
        (r"πριν\s+από\s+(\d+)\s+ημέρ", "days"),
        (r"πριν\s+από\s+(\d+)\s+εβδομ", "weeks"),
        (r"πριν\s+(\d+)\s+δευτερ", "seconds"),
        (r"πριν\s+(\d+)\s+λεπτ", "minutes"),
        (r"πριν\s+(\d+)\s+ώρ", "hours"),
        (r"πριν\s+(\d+)\s+ημέρ", "days"),
        (r"πριν\s+(\d+)\s+εβδομ", "weeks"),
    ]
    # Turkish variants.
    turkish = [
        (r"(\d+)\s+saniye\s+önce", "seconds"),
        (r"(\d+)\s+dakika\s+önce", "minutes"),
        (r"(\d+)\s+saat\s+önce", "hours"),
        (r"(\d+)\s+gün\s+önce", "days"),
        (r"(\d+)\s+hafta\s+önce", "weeks"),
    ]
    for pattern, unit in english + greek + turkish:
        match = re.search(pattern, text)
        if match:
            return now - timedelta(**{unit: int(match.group(1))})

    if "yesterday" in text or "χθες" in text or "dün" in text:
        return now - timedelta(days=1)

    # Explicitly reject month/year ages instead of accidentally dating them as now.
    old_markers = ["month", "months", "year", "years", "μήνα", "μήνες", "μην", "έτος", "χρόν", "ay önce", "yıl önce"]
    if any(marker in text for marker in old_markers):
        return None
    return None


def collect_analyst_youtube(analysts):
    """Search YouTube and keep only videos whose displayed age is <= 72 hours."""
    results = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=72)
    for analyst in analysts:
        query = quote_plus(analyst["name"])
        # YouTube's upload-date filter helps discovery, but our own timestamp gate is authoritative.
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
            # Unknown date is rejected; old videos can never masquerade as fresh.
            if published_dt is None or published_dt < cutoff:
                continue
            results.append({"title": f"[ANALYST WATCH · YOUTUBE] {analyst['name']} — {title}", "url": f"https://www.youtube.com/watch?v={video_id}", "source": f"YouTube · {owner or analyst['name']}", "published": published_dt.isoformat(), "published_ts": published_dt.timestamp(), "scores": {"geopolitico": 80}, "matched_keywords": {"geopolitico": [analyst["name"], "YouTube"]}, "content_type": "analyst", "analyst": analyst["name"], "analyst_country": analyst["country"]})
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
    for extra_sources_name in ("pontos_greek_sources.yaml", "sportdog_sources.yaml", "geopolitico_turkey_sources.yaml", "geopolitico_greek_sources.yaml", "geopolitico_analysis_sources.yaml"):
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
    first_seen = load_first_seen()
    now = datetime.now(timezone.utc)
    items = []
    for source in source_cfg.get("sources", []):
        items.extend(collect_source(source, keywords, modes))

    mentions_path = ROOT / "config" / "mentions.yaml"
    if mentions_path.exists():
        mention_cfg = (load_yaml(mentions_path) or {}).get("mentions", {})
        items.extend(collect_mentions(mention_cfg))

    frontpage_path = ROOT / "config" / "frontpage_sources.yaml"
    if frontpage_path.exists():
        for source in (load_yaml(frontpage_path) or {}).get("frontpages", []):
            items.extend(collect_frontpage_source(source, keywords, modes))

    analysts_path = ROOT / "config" / "analysts.yaml"
    if analysts_path.exists():
        analysts = (load_yaml(analysts_path) or {}).get("analysts", [])
        items.extend(collect_analyst_news(analysts))
        items.extend(collect_analyst_youtube(analysts))

    items = [enrich_item(item, first_seen, now) for item in items]
    items = dedupe_items(items)
    items = group_reproductions(items)
    items.sort(key=lambda item: (item.get("published_ts", 0), max(item["scores"].values())), reverse=True)
    for item in items:
        item.pop("published_ts", None)

    DATA_DIR.mkdir(exist_ok=True)
    payload = {"generated_at": now.isoformat(), "last_successful_collection": now.isoformat(), "count": len(items), "items": items[:750]}
    (DATA_DIR / "news.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    health_payload = {"generated_at": now.isoformat(), "sources": SOURCE_HEALTH, "errors": [x for x in SOURCE_HEALTH if x.get("status") != "ok"]}
    (DATA_DIR / "source_health.json").write_text(json.dumps(health_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Collected {len(items)} relevant stories")


if __name__ == "__main__":
    main()

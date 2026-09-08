import json
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def merge_rules(base: dict, extra: dict) -> dict:
    merged = {"high": {}, "medium": {}}
    for group in ("high", "medium"):
        merged[group].update(base.get(group, {}))
        merged[group].update(extra.get(group, {}))
    return merged


def score_text(text: str, rules: dict):
    lowered = text.casefold()
    score = 0
    matched = []
    for group in ("high", "medium"):
        for keyword, points in rules.get(group, {}).items():
            if keyword.casefold() in lowered:
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
        if max_age_days is not None and published_dt is not None:
            if published_dt < now - timedelta(days=int(max_age_days)):
                continue

        scores = {}
        matches = {}
        text = f"{title} {summary}"
        for mode in source.get("modes", []):
            raw_score, matched = score_text(text, keywords.get(mode, {}))
            weighted = min(100, round(raw_score * float(source.get("source_weight", 1))))
            threshold = int(modes.get(mode, {}).get("threshold", 0))
            if weighted >= threshold:
                scores[mode] = weighted
                matches[mode] = matched

        if scores:
            results.append({
                "title": title,
                "url": url,
                "source": source["name"],
                "published": published_raw,
                "published_ts": published_dt.timestamp() if published_dt else 0,
                "scores": scores,
                "matched_keywords": matches,
            })
    return results


def main():
    source_cfg = load_yaml(ROOT / "config" / "sources.yaml") or {"sources": []}

    for extra_sources_name in (
        "pontos_greek_sources.yaml",
        "sportdog_sources.yaml",
        "geopolitico_turkey_sources.yaml",
    ):
        extra_sources_path = ROOT / "config" / extra_sources_name
        if extra_sources_path.exists():
            extra_sources = load_yaml(extra_sources_path) or {}
            source_cfg.setdefault("sources", []).extend(extra_sources.get("sources", []))

    keywords = load_yaml(ROOT / "config" / "keywords.yaml")
    for extra_name in ("pontos_turkey_keywords.yaml", "pontos_greek_keywords.yaml"):
        extra_path = ROOT / "config" / extra_name
        if extra_path.exists():
            pontos_extra = load_yaml(extra_path) or {}
            keywords["pontos_voice"] = merge_rules(keywords.get("pontos_voice", {}), pontos_extra)

    sportdog_extra_path = ROOT / "config" / "sportdog_extra_keywords.yaml"
    if sportdog_extra_path.exists():
        sportdog_extra = load_yaml(sportdog_extra_path) or {}
        keywords["sportdog"] = merge_rules(keywords.get("sportdog", {}), sportdog_extra)

    geopolitico_turkey_keywords = ROOT / "config" / "geopolitico_turkey_keywords.yaml"
    if geopolitico_turkey_keywords.exists():
        turkey_extra = load_yaml(geopolitico_turkey_keywords) or {}
        keywords["geopolitico"] = merge_rules(keywords.get("geopolitico", {}), turkey_extra)

    modes = load_yaml(ROOT / "config" / "modes.yaml").get("modes", {})

    items = []
    for source in source_cfg.get("sources", []):
        items.extend(collect_source(source, keywords, modes))

    items.sort(
        key=lambda item: (max(item["scores"].values()), item.get("published_ts", 0)),
        reverse=True,
    )

    for item in items:
        item.pop("published_ts", None)

    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items[:500],
    }
    (DATA_DIR / "news.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Collected {len(items)} relevant stories")


if __name__ == "__main__":
    main()

import json
from datetime import datetime, timezone
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


def collect_source(source: dict, keywords: dict, modes: dict):
    results = []
    feed = feedparser.parse(source["url"])
    for entry in feed.entries[:50]:
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        summary = (entry.get("summary") or "").strip()
        if not title or not url:
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
                "published": entry.get("published") or entry.get("updated") or "",
                "scores": scores,
                "matched_keywords": matches,
            })
    return results


def main():
    source_cfg = load_yaml(ROOT / "config" / "sources.yaml")
    keywords = load_yaml(ROOT / "config" / "keywords.yaml")
    pontos_extra_path = ROOT / "config" / "pontos_turkey_keywords.yaml"
    if pontos_extra_path.exists():
        pontos_extra = load_yaml(pontos_extra_path) or {}
        keywords["pontos_voice"] = merge_rules(keywords.get("pontos_voice", {}), pontos_extra)
    modes = load_yaml(ROOT / "config" / "modes.yaml").get("modes", {})

    items = []
    for source in source_cfg.get("sources", []):
        items.extend(collect_source(source, keywords, modes))

    items.sort(key=lambda item: max(item["scores"].values()), reverse=True)
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

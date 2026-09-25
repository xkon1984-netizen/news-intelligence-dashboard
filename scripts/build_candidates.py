import json
import hashlib
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

MODE_LIMITS = {
    "sportdog": 15,
    "geopolitico": 8,
    "pontos_voice": 6,
}

MODE_MAX_AGE_HOURS = {
    "sportdog": 36,
    "geopolitico": 30,
    "pontos_voice": 48,
}

SPORT_PRIORITY_TERMS = {
    "transfer": 12, "transfers": 12, "μεταγραφή": 14, "μεταγραφές": 14,
    "agreement": 12, "deal": 10, "συμφωνία": 12, "υπέγραψε": 14,
    "advanced talks": 12, "medical": 10, "injury": 9, "τραυματισμός": 9,
    "aek": 8, "αεκ": 8, "olympiacos": 8, "ολυμπιακός": 8,
    "panathinaikos": 8, "παναθηναϊκός": 8, "paok": 8, "παοκ": 8,
}


def parse_date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except Exception:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def external_id(mode, url, title):
    raw = f"{mode}|{url}|{title}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def freshness_bonus(published_dt, now):
    if not published_dt:
        return 0
    hours = max(0, (now - published_dt).total_seconds() / 3600)
    if hours <= 3:
        return 15
    if hours <= 8:
        return 10
    if hours <= 18:
        return 6
    if hours <= 30:
        return 3
    return 0


def sports_bonus(title):
    text = title.casefold()
    bonus = 0
    for term, points in SPORT_PRIORITY_TERMS.items():
        if term.casefold() in text:
            bonus += points
    return min(bonus, 30)


def candidate_score(item, mode, now):
    base = int(item.get("scores", {}).get(mode, 0))
    published_dt = parse_date(item.get("published"))
    score = base + freshness_bonus(published_dt, now)
    if mode == "sportdog":
        score += sports_bonus(item.get("title", ""))
    return min(score, 130), published_dt


def build_mode(items, mode, now):
    max_age = MODE_MAX_AGE_HOURS[mode]
    ranked = []
    for item in items:
        if mode not in item.get("scores", {}):
            continue
        final_score, published_dt = candidate_score(item, mode, now)
        if published_dt and published_dt < now - timedelta(hours=max_age):
            continue
        ranked.append((final_score, published_dt or datetime.min.replace(tzinfo=timezone.utc), item))

    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)

    output = []
    seen_sources = {}
    for final_score, published_dt, item in ranked:
        source = item.get("source", "Unknown")
        # Keep the desk diverse: no single source should dominate.
        per_source_cap = 4 if mode == "sportdog" else 3
        if seen_sources.get(source, 0) >= per_source_cap:
            continue
        seen_sources[source] = seen_sources.get(source, 0) + 1

        matched = item.get("matched_keywords", {}).get(mode, [])
        priority = "URGENT" if final_score >= 95 else ("High" if final_score >= 70 else "Normal")
        output.append({
            "external_id": external_id(mode, item.get("url", ""), item.get("title", "")),
            "mode": mode,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": source,
            "published": item.get("published", ""),
            "base_score": int(item.get("scores", {}).get(mode, 0)),
            "candidate_score": final_score,
            "priority": priority,
            "matched_keywords": matched,
            "content_type": item.get("content_type", "news"),
        })
        if len(output) >= MODE_LIMITS[mode]:
            break
    return output


def main():
    source = DATA / "news.json"
    if not source.exists():
        raise SystemExit("data/news.json not found")
    payload = json.loads(source.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    now = datetime.now(timezone.utc)

    modes = {mode: build_mode(items, mode, now) for mode in MODE_LIMITS}
    flat = []
    for mode in ("sportdog", "geopolitico", "pontos_voice"):
        flat.extend(modes[mode])

    result = {
        "generated_at": now.isoformat(),
        "counts": {mode: len(rows) for mode, rows in modes.items()},
        "modes": modes,
        "items": flat,
    }
    (DATA / "daily_candidates.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Daily candidates:", result["counts"])


if __name__ == "__main__":
    main()

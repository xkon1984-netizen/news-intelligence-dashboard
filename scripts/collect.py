from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


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


def main():
    sources = load_yaml(ROOT / "config" / "sources.yaml")
    keywords = load_yaml(ROOT / "config" / "keywords.yaml")
    modes = load_yaml(ROOT / "config" / "modes.yaml")
    print(f"Loaded {len(sources.get('sources', []))} sources")
    print(f"Loaded {len(keywords)} keyword sets")
    print(f"Loaded {len(modes.get('modes', {}))} modes")


if __name__ == "__main__":
    main()

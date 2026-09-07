# News Intelligence Dashboard

A lightweight news monitoring dashboard for three editorial modes:

- Geopolitico
- Pontos Voice
- Sportdog

## MVP scope

The system discovers stories from configured RSS feeds, scores them with deterministic keyword rules, stores only the original article metadata and link, and displays the results in a static dashboard.

It does not rewrite articles and does not use AI APIs.

## Structure

- `config/sources.yaml` — monitored feeds and source weights
- `config/keywords.yaml` — scoring keywords per mode
- `config/modes.yaml` — mode thresholds
- `scripts/collect.py` — RSS collector and scorer
- `data/news.json` — generated feed consumed by the dashboard
- `dashboard/` — static dashboard
- `.github/workflows/news-monitor.yml` — scheduled collector
- `.github/workflows/deploy-pages.yml` — dashboard deployment

## Update cadence

The collector is configured to run every 15 minutes after the workflow reaches the default branch.

## Next steps

1. Validate each starter feed.
2. Expand source lists for all three editorial modes.
3. Tune keyword scores and thresholds from real results.
4. Add stronger duplicate detection and source-language metadata.

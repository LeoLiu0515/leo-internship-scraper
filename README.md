# leo-internship-scraper

Small helper repo for Leo's Summer 2027 internship dashboard automation.

## Why this exists

The main dashboard refresh runs in a sandboxed cloud environment whose network
policy blocks direct requests to third-party ATS APIs (Greenhouse, Workday,
Lever all return 403 Forbidden from that sandbox — only `raw.githubusercontent.com`
and a few package registries are allowed through).

GitHub Actions runners have unrestricted outbound network access. This repo's
`scan.py` runs there on a daily schedule, fetches the direct-company ATS APIs
that the sandbox can't reach, and commits the result to `data.json`. The
dashboard's own `daily_scan.py` then reads `data.json` from this repo via its
`raw.githubusercontent.com` URL — a domain the sandbox *does* allow — closing
the loop without needing any change to the sandbox's network policy.

```
GitHub Actions (unrestricted network)
  -> scan.py fetches Greenhouse/Workday/Lever APIs directly
  -> commits data.json to this repo
  -> dashboard's cloud routine reads data.json via raw.githubusercontent.com
  -> merges into the daily internship dashboard
```

## Companies covered (verified working, hand-tested before adding)

- **Greenhouse**: Zipline, Axon, Verkada
- **Workday**: NVIDIA, Vermeer, Stanley Black & Decker, Johnson & Johnson
- **Lever**: The Voleon Group

Adding a company: test its API endpoint manually first (a guessed board
token/tenant just silently returns nothing, it won't error). Only add it to
`scan.py` once you've confirmed with a real `curl` call that it returns data.

## Schedule

Runs daily at 19:30 UTC (3:30am Taipei), ~30 minutes before the main
dashboard routine reads this repo, so fresh data is ready in time.

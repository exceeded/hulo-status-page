# hulo-status-page

Live service status for Hulo Global's Vendure plugins and licence shop.

Published at **status.huloglobal.com** (via GitHub Pages) — open-source so anyone
can audit how it's measured.

## How it works

A Python probe at `probe.py` hits every customer-facing endpoint, expects a
specific HTTP status per route, and writes:

- `data/history.json` — rolling 90 days of probe results
- `public/index.html` — the rendered status page
- `public/data.json`  — same data, machine-readable

`.github/workflows/probe.yml` runs the probe **every 5 minutes**, commits the
fresh data, then publishes `public/` to GitHub Pages. The action also POSTs to
`HULO_SLACK_WEBHOOK` on first detection of a failure.

Total infra cost: £0/month — runs entirely on GitHub free tier.

## What's probed

Public:
- huloglobal.com/vendure-plugins/ (200)
- huloglobal.com/legal/{terms,privacy}/ (200)
- elite.charity/licence/buy/&lt;plugin&gt; (200) — all three
- elite.charity/licence/forgot (200)
- elite.charity/licence/privacy (200)
- elite.charity/geo-block/presets (200)
- elite.charity/licence/revoked.json (200)

Auth-gated (expected to return 401 = "endpoint reachable, auth required"):
- /email-track/status, /geo-block/status, /ees/visitors/status

A non-200 (or non-expected-status) for &gt;1 run triggers a Slack notification.

## Run locally

```bash
python3 probe.py
open public/index.html
```

## Add or change a check

Edit the `CHECKS` list at the top of `probe.py`. Push — the next scheduled
run picks it up.

## DNS

`status.huloglobal.com` is a `CNAME` to `<owner>.github.io`. The `CNAME`
file in this repo declares the custom domain to GitHub Pages.

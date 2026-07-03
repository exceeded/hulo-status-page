#!/usr/bin/env python3
"""
Probe every customer-facing endpoint and emit:

  - data/history.json — rolling 90 days of {timestamp, results[]}
  - public/index.html — static page rendered from the latest data
  - public/data.json  — same data, machine-readable

Run by GitHub Actions every 5 minutes. The repo is also configured to
serve `public/` via GitHub Pages at status.huloglobal.com (or
<owner>.github.io/hulo-status-page).
"""
import datetime as dt
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).parent
HISTORY = ROOT / 'data' / 'history.json'
PUBLIC = ROOT / 'public'
PUBLIC.mkdir(exist_ok=True)
HISTORY.parent.mkdir(exist_ok=True)

CHECKS = [
    {'name': 'Marketing site',          'url': 'https://huloglobal.com/vendure-plugins/',                            'expect': 200},
    {'name': 'Legal — Terms',           'url': 'https://huloglobal.com/legal/terms/',                                'expect': 200},
    {'name': 'Legal — Privacy',         'url': 'https://huloglobal.com/legal/privacy/',                              'expect': 200},
    {'name': 'Buy — Email Tracking',    'url': 'https://elite.charity/licence/buy/vendure-plugin-email-tracking',    'expect': 200},
    {'name': 'Buy — Geo Block',         'url': 'https://elite.charity/licence/buy/vendure-plugin-geo-block',         'expect': 200},
    {'name': 'Buy — Visitor Analytics', 'url': 'https://elite.charity/licence/buy/vendure-plugin-visitor-analytics', 'expect': 200},
    {'name': 'Forgot key',              'url': 'https://elite.charity/licence/forgot',                               'expect': 200},
    {'name': 'Privacy request',         'url': 'https://elite.charity/licence/privacy',                              'expect': 200},
    {'name': 'Geo-block presets',       'url': 'https://elite.charity/geo-block/presets',                            'expect': 200},
    {'name': 'Email-track status',      'url': 'https://elite.charity/email-track/status',                           'expect': 401},
    {'name': 'Geo-block status',        'url': 'https://elite.charity/geo-block/status',                             'expect': 401},
    {'name': 'Visitor status',          'url': 'https://elite.charity/ees/visitors/status',                          'expect': 401},
    {'name': 'Revocation list',         'url': 'https://elite.charity/licence/revoked.json',                         'expect': 200},
    # Self-check: the status page itself must be publicly reachable at
    # both the custom domain AND the GitHub Pages URL. If someone puts a
    # Cloudflare Access rule on the custom domain, the /login.html redirect
    # would make this check fail — we want to know about it here rather
    # than have customers report it.
    {'name': 'Status page — custom domain', 'url': 'https://status.huloglobal.com/',                                 'expect': 200},
    {'name': 'Status page — Pages URL',     'url': 'https://exceeded.github.io/hulo-status-page/',                   'expect': 200},
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects so the probe sees the actual status
    code — otherwise Cloudflare-Access-style login redirects would
    silently mask endpoints that require authentication."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def probe_one(c):
    start = time.monotonic()
    try:
        req = urllib.request.Request(c['url'], method='GET', headers={'User-Agent': 'HuloStatusBot/1.0'})
        with _opener.open(req, timeout=12) as resp:
            ms = round((time.monotonic() - start) * 1000)
            status = resp.status
            ok = status == c['expect']
            return {'ok': ok, 'status': status, 'ms': ms, 'err': None if ok else f'HTTP {status}'}
    except urllib.error.HTTPError as e:
        ms = round((time.monotonic() - start) * 1000)
        ok = e.code == c['expect']
        # 3xx is an HTTPError with our NoRedirect handler — surface it
        # so operators see, e.g., a 302 to /login.html on what should
        # be a 200 landing page.
        err = None if ok else (
            'redirect to login (auth in front of endpoint?)'
            if e.code in (301, 302, 303, 307, 308)
            else f'HTTP {e.code}'
        )
        return {'ok': ok, 'status': e.code, 'ms': ms, 'err': err}
    except Exception as e:
        ms = round((time.monotonic() - start) * 1000)
        return {'ok': False, 'status': 0, 'ms': ms, 'err': str(e)[:200]}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    for c in CHECKS:
        r = probe_one(c)
        results.append({'name': c['name'], 'url': c['url'], **r})

    # Rolling history — keep the last 90 days at 5-min intervals
    if HISTORY.exists():
        history = json.loads(HISTORY.read_text())
    else:
        history = []
    history.append({'t': now.isoformat(), 'results': results})
    cutoff = now - dt.timedelta(days=90)
    history = [h for h in history if dt.datetime.fromisoformat(h['t']) > cutoff]
    HISTORY.write_text(json.dumps(history, separators=(',', ':')))

    # Per-check uptime rollups for the page
    summary = []
    for c in CHECKS:
        slug = c['name']
        recent = [h for h in history if any(r['name'] == slug for r in h['results'])][-288:]  # last 24h at 5m
        if not recent:
            uptime_24h, uptime_30d = None, None
        else:
            ups = sum(1 for h in recent for r in h['results'] if r['name'] == slug and r['ok'])
            total = len(recent)
            uptime_24h = round(100.0 * ups / total, 2) if total else None
            d30 = [h for h in history if dt.datetime.fromisoformat(h['t']) > now - dt.timedelta(days=30)]
            ups30 = sum(1 for h in d30 for r in h['results'] if r['name'] == slug and r['ok'])
            total30 = sum(1 for h in d30 for r in h['results'] if r['name'] == slug)
            uptime_30d = round(100.0 * ups30 / total30, 2) if total30 else None
        latest = next((r for r in results if r['name'] == slug), None)
        summary.append({
            'name': slug,
            'url': c['url'],
            'ok': latest['ok'] if latest else False,
            'status': latest['status'] if latest else 0,
            'ms': latest['ms'] if latest else 0,
            'err': latest['err'] if latest else 'no data',
            'uptime_24h': uptime_24h,
            'uptime_30d': uptime_30d,
        })

    overall_ok = all(s['ok'] for s in summary)
    overall = 'All systems operational' if overall_ok else 'Degraded service'

    # Write machine-readable for any downstream consumer
    (PUBLIC / 'data.json').write_text(json.dumps({
        'updated': now.isoformat(),
        'overall': overall,
        'overall_ok': overall_ok,
        'checks': summary,
    }, indent=2))

    # Write the static page
    rows = []
    for s in summary:
        badge = '<span class="ok">●</span>' if s['ok'] else '<span class="err">●</span>'
        u24 = f"{s['uptime_24h']}%" if s['uptime_24h'] is not None else '—'
        u30 = f"{s['uptime_30d']}%" if s['uptime_30d'] is not None else '—'
        err = '' if s['ok'] else f'<div class="err-msg">{s["err"] or f"HTTP {s[chr(39)+chr(115)+chr(116)+chr(97)+chr(116)+chr(117)+chr(115)+chr(39)]}"}</div>'
        rows.append(
            f'<tr>'
            f'<td>{badge} {s["name"]}</td>'
            f'<td class="rt">{s["ms"]} ms</td>'
            f'<td class="rt">{u24}</td>'
            f'<td class="rt">{u30}</td>'
            f'</tr>'
        )
        if not s['ok']:
            rows.append(f'<tr><td colspan="4" class="err-row">{s["err"] or f"HTTP {s[chr(39)+chr(115)+chr(116)+chr(97)+chr(116)+chr(117)+chr(115)+chr(39)]}"}: <code>{s["url"]}</code></td></tr>')

    overall_class = 'ok' if overall_ok else 'err'
    rendered = (PUBLIC / 'index.html')
    rendered.write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hulo Global — Status</title>
<meta name="description" content="Real-time service status for Hulo Global Vendure plugins and the licence shop.">
<style>
  :root {{ color-scheme: light; }}
  *,*::before,*::after {{ box-sizing: border-box; }}
  body {{ margin: 0; font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f8fafc; color: #1f2937; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 48px 24px; }}
  header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 28px; }}
  header .logo {{ width: 36px; height: 36px; border-radius: 8px; background: #0f172a; color: #fff; display: grid; place-items: center; font-weight: 700; }}
  header h1 {{ font-size: 18px; margin: 0; color: #1e293b; }}
  .overall {{ padding: 18px 22px; border-radius: 12px; margin-bottom: 28px; font-size: 18px; font-weight: 600; }}
  .overall.ok {{ background: #ecfdf5; color: #065f46; border: 1px solid #6ee7b7; }}
  .overall.err {{ background: #fef2f2; color: #991b1b; border: 1px solid #fca5a5; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; font-size: 14px; }}
  th {{ text-align: left; padding: 10px 14px; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #64748b; background: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
  th.rt, td.rt {{ text-align: right; }}
  td {{ padding: 12px 14px; border-bottom: 1px solid #f1f5f9; }}
  tr:last-child td {{ border-bottom: 0; }}
  .ok {{ color: #10b981; font-size: 18px; line-height: 1; }}
  .err {{ color: #ef4444; font-size: 18px; line-height: 1; }}
  .err-row {{ background: #fef2f2; color: #991b1b; font-size: 12px; }}
  .err-row code {{ font-size: 11px; background: #fff; padding: 1px 6px; border-radius: 3px; border: 1px solid #fca5a5; }}
  .updated {{ color: #64748b; font-size: 13px; margin-top: 18px; text-align: center; }}
  footer {{ margin-top: 32px; color: #94a3b8; font-size: 12px; text-align: center; }}
  footer a {{ color: #475569; }}
  @media (max-width: 600px) {{ main {{ padding: 24px 14px; }} th, td {{ padding: 10px 8px; font-size: 13px; }} }}
</style>
</head>
<body>
<main>
<header>
<div class="logo">HG</div>
<h1>Hulo Global — Service Status</h1>
</header>

<div class="overall {overall_class}">{overall}</div>

<table>
<thead><tr><th>Service</th><th class="rt">Latency</th><th class="rt">24h</th><th class="rt">30d</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>

<p class="updated">Last checked {now.strftime('%Y-%m-%d %H:%M UTC')} · refreshes every 5 minutes</p>

<footer>
<p>This page is generated by an open-source <a href="https://github.com/exceeded/hulo-status-page">probe</a> running every 5 minutes from GitHub Actions. Raw data: <a href="data.json">data.json</a>.</p>
<p>Hulo Global Limited · UK Companies House 17134928 · <a href="https://huloglobal.com">huloglobal.com</a></p>
</footer>
</main>
</body>
</html>
""", encoding='utf-8')

    print(f'overall={overall_ok}  failing={[s["name"] for s in summary if not s["ok"]]}')


if __name__ == '__main__':
    main()

# GEO/AEO Schema Audit

Site-wide structured data audit tool. Point it at a domain, it reads the
XML sitemap, crawls the pages it finds, and reports on:

- Which schema.org types are present on each page (parsed directly from
  JSON-LD, not by scripting Google's single-page testing tool)
- Missing required vs. recommended fields per type, for rich-result
  eligibility
- Whether known AI crawlers (GPTBot, ClaudeBot, PerplexityBot,
  Google-Extended, etc.) are blocked in robots.txt
- Whether an `llms.txt` file is present

## v1 scope, on purpose

- Caps at 25 pages per scan and runs synchronously (no job queue yet) --
  keeps the architecture simple. Raise `MAX_URLS` in `app.py` once the core
  pipeline is proven against real sites.
- Reads JSON-LD and microdata (via `extruct`). RDFa is deliberately left out
  -- real-world adoption has dropped enough that it's a poor use of effort
  right now.
- The `PRIORITY_TYPES` required/recommended field lists in `geo_rules.py`
  are a starting point based on general knowledge of Google's rich-result
  guidelines -- worth cross-checking and refining against
  https://developers.google.com/search/docs/appearance/structured-data
  before treating the output as authoritative.
- The live schema.org vocabulary lookup (used to flag genuinely unrecognized
  types) needs normal outbound internet access. It'll work once deployed;
  if it can't reach schema.org for any reason, that check is skipped
  gracefully rather than breaking the scan.
- Robots.txt handling uses `protego`, not the standard library's
  `robotparser` -- stdlib only implements the original 1996 spec and
  silently ignores the `*`/`$` wildcard syntax that most real enterprise
  robots.txt files now depend on. It also fetches robots.txt itself rather
  than letting a library fetch it internally, so it correctly distinguishes
  a 401/403 from bot-management (we can't confirm the real policy) from a
  genuine rules-based block.

## Running locally

```
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000

## Deploying on Render

1. Push this code to a GitHub repository.
2. On Render: New > Web Service > connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --timeout 60`
5. Leave everything else as default and deploy.

The `--timeout 60` matters: with `MAX_URLS = 50` and 10 concurrent workers,
a worst-case scan (several slow/unresponsive pages) can take close to a
minute. gunicorn's default 30-second worker timeout would kill the request
mid-scan and you'd see a 502 instead of a report. If you raise `MAX_URLS`
further, raise this timeout to match.

Render's free tier spins your service down after 15 minutes of no traffic,
so the first request after a quiet period takes 30-60 seconds to wake back
up. Fine for demos; upgrade to a paid instance later if it needs to stay
warm for real traffic.

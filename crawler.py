"""
crawler.py

Fetches a batch of URLs concurrently. Checks robots.txt before crawling so
the tool behaves like a well-mannered bot, not just an audit script.

Uses protego rather than the standard library's urllib.robotparser: stdlib
only implements the original 1996 robots.txt spec (plain prefix matching).
It silently ignores the '*' wildcard and '$' end-anchor syntax that Google
introduced and that most real-world enterprise robots.txt files now rely on
heavily (Samsung's, for one, uses '/*/parking', '/*/system/*',
'*jsessionid=*' throughout). Without wildcard support, those rules just
never match anything -- a real correctness bug, not an edge case.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from protego import Protego

USER_AGENT = "GEOAuditBot/0.1 (+https://github.com/; site auditing tool)"
TIMEOUT = 10
MAX_WORKERS = 10  # raised alongside MAX_URLS; threads are cheap while blocked on I/O
MAX_PAGE_BYTES = 3_000_000  # ~3MB; legitimate single-page HTML is essentially never this large.
# Defends against a pathological response (huge page, or a non-HTML resource
# accidentally listed in the sitemap) getting fully parsed by BeautifulSoup
# AND extruct -- on a 512MB host that's a real way to get OOM-killed, not
# just a slow request.


def _robots_parser_for(domain):
    """Fetches robots.txt with our own real User-Agent header (rather than
    a library's internal fetch, which might silently use a different,
    easily-blocked default UA).

    Returns (parser_or_none, status) where status is one of:
      'ok'             -- robots.txt fetched and parsed; parser has real rules
      'access_denied'  -- got a 401/403 reading robots.txt itself. We can't
                          tell what the real policy is, so we default to NOT
                          crawling (the cautious choice for a polite bot) --
                          but this gets flagged distinctly so reporting can
                          say "couldn't confirm" rather than "disallowed."
      'unavailable'    -- no robots.txt, or some other fetch failure. Standard
                          convention: absence of a file means crawling is
                          allowed.
    """
    parsed = urlparse(domain)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException:
        return None, "unavailable"

    if resp.status_code in (401, 403):
        return None, "access_denied"
    if resp.status_code >= 400:
        return None, "unavailable"
    return Protego.parse(resp.text), "ok"


def _fetch_one(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if len(resp.content) > MAX_PAGE_BYTES:
            return url, None, resp.status_code, f"Page too large to parse safely ({len(resp.content):,} bytes)"
        return url, resp.text, resp.status_code, None
    except requests.RequestException as exc:
        return url, None, None, str(exc)


def fetch_pages(urls, domain):
    """Returns (results, skipped, robots_access_denied). results is a
    GENERATOR, not a list -- pages are yielded as each fetch completes
    rather than collected into one list first. That matters: collecting
    everything upfront means peak memory during the fetch phase scales with
    how many total pages are being scanned (MAX_URLS), even though only
    MAX_WORKERS are ever actually in flight at once. Streaming decouples
    those two numbers -- peak memory now tracks concurrency, not sample
    size, so raising MAX_URLS doesn't proportionally raise this risk.

    robots_access_denied is True specifically when fetching robots.txt
    itself got a 401/403 -- meaning we can't actually tell what the
    published policy says, as distinct from genuinely being disallowed by
    real rules. Both this and `skipped` are known before any page fetch
    starts, so they're returned immediately rather than needing the
    generator to be consumed first."""
    rp, status = _robots_parser_for(domain)

    if status == "access_denied":
        allowed_urls = []  # cautious default when we can't confirm permissions
    elif rp is None:
        allowed_urls = list(urls)  # no robots.txt found -- allowed by convention
    else:
        allowed_urls = [u for u in urls if rp.can_fetch(u, USER_AGENT)]

    skipped = len(urls) - len(allowed_urls)

    def _stream():
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_one, url): url for url in allowed_urls}
            for future in as_completed(futures):
                url, html, status_code, error = future.result()
                yield {
                    "url": url,
                    "html": html,
                    "status_code": status_code,
                    "error": error,
                }

    return _stream(), skipped, (status == "access_denied")

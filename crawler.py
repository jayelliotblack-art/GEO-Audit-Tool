"""
crawler.py

Fetches a batch of URLs concurrently. Checks robots.txt before crawling so the
tool behaves like a well-mannered bot, not just an audit script.
"""

import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

USER_AGENT = "GEOAuditBot/0.1 (+https://github.com/; site auditing tool)"
TIMEOUT = 10
MAX_WORKERS = 10  # raised alongside MAX_URLS; threads are cheap while blocked on I/O


def _robots_parser_for(domain):
    parsed = urlparse(domain)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        return None  # if robots.txt can't be read, default to allowing the crawl
    return rp


def _fetch_one(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return url, resp.text, resp.status_code, None
    except requests.RequestException as exc:
        return url, None, None, str(exc)


def fetch_pages(urls, domain):
    """Returns a list of dicts: {url, html, status_code, error}.
    Skips URLs disallowed by robots.txt for our user-agent."""
    rp = _robots_parser_for(domain)
    allowed_urls = []
    for url in urls:
        if rp is None or rp.can_fetch(USER_AGENT, url):
            allowed_urls.append(url)

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, url): url for url in allowed_urls}
        for future in as_completed(futures):
            url, html, status_code, error = future.result()
            results.append({
                "url": url,
                "html": html,
                "status_code": status_code,
                "error": error,
            })

    skipped = len(urls) - len(allowed_urls)
    return results, skipped

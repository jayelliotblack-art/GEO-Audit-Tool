"""
sitemap.py

Finds and parses a site's XML sitemap, including sitemap index files
(a sitemap that just points to other sitemaps, common on larger sites).
"""

import gzip
import io
import xml.etree.ElementTree as ET

import requests

USER_AGENT = "GEOAuditBot/0.1 (+https://github.com/; site auditing tool)"
TIMEOUT = 10
MAX_SITEMAPS_TO_FOLLOW = 5   # cap how many sub-sitemaps we'll follow from an index
MAX_URLS_TOTAL = 500        # hard ceiling so a huge site can't hang the scan


def _fetch(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    content = resp.content
    # Some sitemaps are served gzipped (file extension .xml.gz) rather than
    # using HTTP content-encoding (which requests already decodes for us).
    if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return content


def _parse_xml(content):
    # Sitemaps use a default namespace; strip it so tag lookups are simple.
    root = ET.fromstring(content)
    tag = root.tag.split("}")[-1]
    children = []
    for child in root:
        ctag = child.tag.split("}")[-1]
        loc = None
        for sub in child:
            stag = sub.tag.split("}")[-1]
            if stag == "loc":
                loc = sub.text.strip() if sub.text else None
        if loc:
            children.append(loc)
    return tag, children  # tag is "sitemapindex" or "urlset"


def discover_sitemap_url(domain):
    """Try the conventional /sitemap.xml location, falling back to robots.txt's
    Sitemap: directive if present."""
    domain = domain.rstrip("/")
    candidate = f"{domain}/sitemap.xml"
    try:
        requests.head(candidate, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return candidate
    except requests.RequestException:
        pass

    try:
        robots = requests.get(f"{domain}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        for line in robots.text.splitlines():
            if line.lower().startswith("sitemap:"):
                return line.split(":", 1)[1].strip()
    except requests.RequestException:
        pass

    return candidate  # last resort guess; the fetch step will surface the error


def get_sitemap_urls(domain):
    """Returns (urls, error). urls is a list of page URLs (capped at
    MAX_URLS_TOTAL). error is None on success, or a human-readable string
    describing what went wrong."""
    sitemap_url = discover_sitemap_url(domain)

    try:
        content = _fetch(sitemap_url)
    except requests.RequestException as exc:
        return [], f"Couldn't fetch a sitemap at {sitemap_url} ({exc})"

    try:
        tag, locs = _parse_xml(content)
    except ET.ParseError as exc:
        return [], f"Sitemap at {sitemap_url} wasn't valid XML ({exc})"

    if tag == "sitemapindex":
        page_urls = []
        for sub_sitemap_url in locs[:MAX_SITEMAPS_TO_FOLLOW]:
            try:
                sub_content = _fetch(sub_sitemap_url)
                _, sub_locs = _parse_xml(sub_content)
                page_urls.extend(sub_locs)
            except (requests.RequestException, ET.ParseError):
                continue  # skip a broken sub-sitemap, don't kill the whole scan
            if len(page_urls) >= MAX_URLS_TOTAL:
                break
        return page_urls[:MAX_URLS_TOTAL], None

    # tag == "urlset" (a normal, non-index sitemap)
    return locs[:MAX_URLS_TOTAL], None

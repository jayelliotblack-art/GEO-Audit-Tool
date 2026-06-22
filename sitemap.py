"""
sitemap.py

Finds and parses a site's XML sitemap, including sitemap index files
(a sitemap that just points to other sitemaps -- common on larger sites,
and sometimes nested two or three levels deep: a root index pointing to
per-category indexes, which point to the actual page sitemaps).
"""

import gzip
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

USER_AGENT = "GEOAuditBot/0.1 (+https://github.com/; site auditing tool)"
TIMEOUT = 10
MAX_SITEMAPS_TO_FOLLOW = 20  # total sitemap *files* fetched, across all nesting levels
MAX_URLS_TOTAL = 500        # hard ceiling on pages actually queued for scanning


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
        loc = None
        for sub in child:
            stag = sub.tag.split("}")[-1]
            if stag == "loc":
                loc = sub.text.strip() if sub.text else None
        if loc:
            children.append(loc)
    return tag, children  # tag is "sitemapindex" or "urlset"


def _url_exists(url):
    """GET rather than HEAD -- some servers don't implement HEAD properly
    for static files and return a misleading status. We need the real
    status code either way, so just do the request we actually need."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def discover_sitemap_url(domain):
    """Sitemap location genuinely varies by site -- some publish per-locale
    sitemaps under a subfolder (e.g. samsung.com/de/sitemap.xml), others
    only ever have one at the true root (e.g. apple.com/sitemap.xml). There
    isn't one rule that's correct everywhere, so this tries, in order:
    1. A sitemap under whatever subfolder the person entered, if any
       (handles the Samsung case)
    2. A sitemap at the true domain root (handles the Apple case)
    3. Whatever robots.txt itself actually advertises via 'Sitemap:' lines
       -- robots.txt always lives at the true root regardless of any
       subfolder in the input, and may list a locale-matching sitemap
       explicitly even when neither guess above happens to work
    """
    parsed = urlparse(domain)
    root = f"{parsed.scheme}://{parsed.netloc}"
    subfolder = parsed.path.rstrip("/")  # "" if no subfolder was entered

    candidates = []
    if subfolder:
        candidates.append(f"{root}{subfolder}/sitemap.xml")
    candidates.append(f"{root}/sitemap.xml")

    for candidate in candidates:
        if _url_exists(candidate):
            return candidate

    try:
        # robots.txt is always at the true root, never under a subfolder
        robots = requests.get(f"{root}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if robots.status_code == 200:
            sitemap_lines = [
                line.split(":", 1)[1].strip()
                for line in robots.text.splitlines()
                if line.lower().startswith("sitemap:")
            ]
            if subfolder:
                locale_segment = subfolder.strip("/")
                locale_match = next(
                    (s for s in sitemap_lines if locale_segment in urlparse(s).path.strip("/").split("/")),
                    None,
                )
                if locale_match:
                    return locale_match
            if sitemap_lines:
                return sitemap_lines[0]
    except requests.RequestException:
        pass

    # Nothing worked -- return the best guess anyway so the caller surfaces
    # a real, specific error instead of a silent failure.
    return candidates[0]


def get_sitemap_urls(domain):
    """Returns (urls, total_found, error).
    - urls: page URLs queued for scanning, capped at MAX_URLS_TOTAL
    - total_found: the REAL count of page URLs actually encountered, before
      that cap -- this is what should be displayed as "X found", not the
      cap itself
    - error: None on success, or a human-readable string

    Recurses through nested sitemap indexes at arbitrary depth (some sites
    nest two or three levels), rather than assuming only one level of
    indexing exists. Tracks visited URLs so a misconfigured site that
    points back at itself can't loop forever.
    """
    sitemap_url = discover_sitemap_url(domain)

    try:
        content = _fetch(sitemap_url)
    except requests.RequestException as exc:
        return [], 0, f"Couldn't fetch a sitemap at {sitemap_url} ({exc})"

    try:
        tag, locs = _parse_xml(content)
    except ET.ParseError as exc:
        return [], 0, f"Sitemap at {sitemap_url} wasn't valid XML ({exc})"

    if tag != "sitemapindex":
        # A normal, single-level sitemap -- every <loc> here is a real page.
        total_found = len(locs)
        return locs[:MAX_URLS_TOTAL], total_found, None

    page_urls = []
    visited = {sitemap_url}
    queue = list(locs)
    sitemaps_fetched = 0

    while queue and sitemaps_fetched < MAX_SITEMAPS_TO_FOLLOW and len(page_urls) < MAX_URLS_TOTAL:
        sub_url = queue.pop(0)
        if sub_url in visited:
            continue
        visited.add(sub_url)
        try:
            sub_content = _fetch(sub_url)
            sub_tag, sub_locs = _parse_xml(sub_content)
        except (requests.RequestException, ET.ParseError):
            continue
        sitemaps_fetched += 1
        if sub_tag == "sitemapindex":
            queue.extend(sub_locs)  # another layer of nesting -- go deeper
        else:
            page_urls.extend(sub_locs)  # real pages

    return page_urls[:MAX_URLS_TOTAL], len(page_urls), None

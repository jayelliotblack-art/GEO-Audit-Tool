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
    """Returns (tag, entries) where entries is a list of {"loc": str,
    "lastmod": str_or_None} -- captures lastmod rather than discarding it,
    since sitemaps often include it for free (no extra request) and it's
    the basis for the content-freshness check."""
    root = ET.fromstring(content)
    tag = root.tag.split("}")[-1]
    entries = []
    for child in root:
        loc = None
        lastmod = None
        for sub in child:
            stag = sub.tag.split("}")[-1]
            if stag == "loc":
                loc = sub.text.strip() if sub.text else None
            elif stag == "lastmod":
                lastmod = sub.text.strip() if sub.text else None
        if loc:
            entries.append({"loc": loc, "lastmod": lastmod})
    return tag, entries  # tag is "sitemapindex" or "urlset"


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
    """Returns (urls, total_found, lastmod_by_url, error).
    - urls: page URLs queued for scanning, capped at MAX_URLS_TOTAL
    - total_found: the REAL count of page URLs actually encountered, before
      that cap -- this is what should be displayed as "X found", not the
      cap itself
    - lastmod_by_url: {url: lastmod_str_or_None} for every url in `urls` --
      captured for free while parsing, no extra requests
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
        return [], 0, {}, f"Couldn't fetch a sitemap at {sitemap_url} ({exc})"

    try:
        tag, entries = _parse_xml(content)
    except ET.ParseError as exc:
        return [], 0, {}, f"Sitemap at {sitemap_url} wasn't valid XML ({exc})"

    if tag != "sitemapindex":
        # A normal, single-level sitemap -- every entry here is a real page.
        total_found = len(entries)
        page_entries = entries[:MAX_URLS_TOTAL]
        urls = [e["loc"] for e in page_entries]
        lastmod_by_url = {e["loc"]: e["lastmod"] for e in page_entries}
        return urls, total_found, lastmod_by_url, None

    page_entries = []
    visited = {sitemap_url}
    queue = [e["loc"] for e in entries]
    sitemaps_fetched = 0

    while queue and sitemaps_fetched < MAX_SITEMAPS_TO_FOLLOW and len(page_entries) < MAX_URLS_TOTAL:
        sub_url = queue.pop(0)
        if sub_url in visited:
            continue
        visited.add(sub_url)
        try:
            sub_content = _fetch(sub_url)
            sub_tag, sub_entries = _parse_xml(sub_content)
        except (requests.RequestException, ET.ParseError):
            continue
        sitemaps_fetched += 1
        if sub_tag == "sitemapindex":
            queue.extend(e["loc"] for e in sub_entries)  # another layer of nesting -- go deeper
        else:
            page_entries.extend(sub_entries)  # real pages

    page_entries = page_entries[:MAX_URLS_TOTAL]
    urls = [e["loc"] for e in page_entries]
    lastmod_by_url = {e["loc"]: e["lastmod"] for e in page_entries}
    return urls, len(page_entries), lastmod_by_url, None

"""
extractor.py

Pulls structured data directly out of a page's HTML. Covers the two formats
that matter in practice: JSON-LD (hand-rolled parsing below, validated
against real sites) and microdata (via extruct, a well-maintained library --
no reason to hand-roll a microdata parser when this exists). RDFa is
deliberately out of scope for now; real-world adoption has dropped enough
that it's a poor use of v1 effort.

parse_html() is called ONCE per page in scorer.py; the resulting soup is
shared across extract_json_ld, extract_meta_robots, and extract_canonical
rather than each of them re-parsing the same HTML from scratch. That matters
more than it looks like it should: on a memory-constrained host (Render's
free tier is 512MB), three to four full re-parses of a large page's markup
is a real way to get OOM-killed, not just a wasted CPU cycle. (extruct,
used for microdata, does its own internal parsing and can't share this --
no public API to feed it a pre-parsed tree.)
"""

import json
import re
from urllib.parse import urljoin, urlparse

import extruct
from bs4 import BeautifulSoup


def parse_html(html):
    """Parses HTML once. Pass the result to extract_json_ld,
    extract_meta_robots, and extract_canonical instead of raw HTML."""
    if not html:
        return None
    return BeautifulSoup(html, "lxml")


def extract_json_ld(soup):
    """Returns a list of dicts, each a parsed JSON-LD object with at least
    an '@type'. Handles scripts containing a single object, a list of
    objects, or a @graph wrapper."""
    if soup is None:
        return []

    blocks = soup.find_all("script", attrs={"type": "application/ld+json"})

    items = []
    for block in blocks:
        if not block.string:
            continue
        try:
            data = json.loads(block.string)
        except (json.JSONDecodeError, TypeError):
            continue  # malformed JSON-LD; this itself is worth flagging later

        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if "@graph" in candidate and isinstance(candidate["@graph"], list):
                items.extend(g for g in candidate["@graph"] if isinstance(g, dict))
            else:
                items.append(candidate)

    return items


def extract_microdata(html, url):
    """Returns a list of {"type": str, "properties": dict} -- normalized to
    the same shape regardless of source format so scorer.py doesn't need to
    care which extraction path an entity came from. Takes raw html (not the
    shared soup) since extruct does its own parsing internally."""
    if not html:
        return []

    try:
        data = extruct.extract(html, base_url=url, syntaxes=["microdata"])
    except Exception:
        return []  # a parsing failure here shouldn't take down the whole scan

    results = []
    for entry in data.get("microdata", []):
        type_url = entry.get("type")
        if not type_url:
            continue
        type_name = type_url.rstrip("/").rsplit("/", 1)[-1]
        results.append({
            "type": type_name,
            "properties": entry.get("properties") or {},
        })
    return results


def extract_meta_robots(soup):
    """Returns the lowercased content of <meta name="robots"> if present,
    else None. A page can be fully allowed by robots.txt and still tell
    crawlers not to index it via this tag -- a different, page-level signal
    robots.txt has no way to show."""
    if soup is None:
        return None
    tag = soup.find("meta", attrs={"name": lambda v: bool(v) and v.lower() == "robots"})
    if tag and tag.get("content"):
        return tag["content"].strip().lower()
    return None


def is_noindexed(meta_robots_content):
    """meta_robots_content is the raw, comma-separated directive string
    (e.g. 'noindex, nofollow'). Checks specifically for 'noindex' as its own
    directive, not just a substring -- avoids a false match on some
    hypothetical future directive that merely contains those letters."""
    if not meta_robots_content:
        return False
    directives = [d.strip() for d in meta_robots_content.split(",")]
    return "noindex" in directives


def extract_canonical(soup, page_url):
    """Returns a list of resolved canonical URLs found on the page (relative
    hrefs resolved against page_url). A well-formed page has exactly one;
    returning all of them rather than just the first lets the caller flag
    the 'more than one canonical tag' case, which is itself a real bug --
    browsers and engines just pick one arbitrarily when that happens."""
    if soup is None:
        return []
    tags = soup.find_all("link", rel=True)
    hrefs = []
    for tag in tags:
        rel = tag.get("rel")
        rel_values = rel if isinstance(rel, list) else [rel]
        if any(r and r.lower() == "canonical" for r in rel_values):
            href = tag.get("href")
            if href:
                hrefs.append(urljoin(page_url, href.strip()))
    return hrefs


def extract_internal_links(soup, page_url):
    """Returns the set of resolved, same-domain link targets found via
    <a href> on this page (trailing slash stripped for comparison). Skips
    non-navigational hrefs (anchors, mailto, tel, javascript). Used to spot
    orphan pages -- ones the sitemap lists but nothing else we scanned
    actually links to."""
    if soup is None:
        return set()
    domain = urlparse(page_url).netloc
    links = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        resolved = urljoin(page_url, href)
        if urlparse(resolved).netloc == domain:
            links.add(resolved.rstrip("/"))
    return links


def extract_visible_text(soup):
    """Returns the page's visible text (script/style content excluded),
    normalized to lowercase with collapsed whitespace -- used to check
    whether schema-claimed content (e.g. an FAQPage's questions) actually
    appears on the page, rather than just being asserted in markup.

    Deliberately non-destructive: walks text nodes and skips ones whose
    parent is <script>/<style>/<noscript>, rather than decompose()-ing those
    tags out of the tree. This same soup object is shared with
    extract_json_ld elsewhere in the pipeline -- mutating it here would
    delete the very <script type="application/ld+json"> tags that function
    still needs to read."""
    if soup is None:
        return ""
    body = soup.find("body") or soup
    texts = [
        str(node) for node in body.find_all(string=True)
        if node.parent.name not in ("script", "style", "noscript")
    ]
    return normalize_for_match(" ".join(texts))


_PUNCT_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...",
}


def normalize_for_match(text):
    """Shared normalization for both the visible-page text and schema-claimed
    text, so typographic differences (curly vs. straight quotes, en/em
    dashes) don't cause a false 'not found on page' mismatch."""
    text = str(text)
    for fancy, plain in _PUNCT_MAP.items():
        text = text.replace(fancy, plain)
    return re.sub(r"\s+", " ", text).strip().lower()


def get_types(item):
    """@type can be a string or a list of strings. Normalize to a list,
    dropping any malformed entries that aren't plain strings (some sites'
    JSON-LD has @type as a nested object, which would otherwise blow up a
    dict lookup downstream)."""
    t = item.get("@type")
    if t is None:
        return []
    candidates = t if isinstance(t, list) else [t]
    return [c for c in candidates if isinstance(c, str)]

"""
extractor.py

Pulls structured data directly out of a page's HTML rather than relying on
any third-party testing tool. Covers JSON-LD (the format ~95% of sites use).
Microdata/RDFa support is a reasonable v1.1 addition, not included here.
"""

import json

from bs4 import BeautifulSoup


def extract_json_ld(html):
    """Returns a list of dicts, each a parsed JSON-LD object with at least
    an '@type'. Handles scripts containing a single object, a list of
    objects, or a @graph wrapper."""
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
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

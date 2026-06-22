"""
extractor.py

Pulls structured data directly out of a page's HTML. Covers the two formats
that matter in practice: JSON-LD (hand-rolled parsing below, validated
against real sites) and microdata (via extruct, a well-maintained library --
no reason to hand-roll a microdata parser when this exists). RDFa is
deliberately out of scope for now; real-world adoption has dropped enough
that it's a poor use of v1 effort.
"""

import json

import extruct
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


def extract_microdata(html, url):
    """Returns a list of {"type": str, "properties": dict} -- normalized to
    the same shape regardless of source format so scorer.py doesn't need to
    care which extraction path an entity came from."""
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


"""
schema_vocab.py

Loads the official schema.org vocabulary so the tool can tell the difference
between "valid schema.org markup" and "typo'd or made-up type/property" --
and so the UI can link straight to the real docs for anything it detects.

NOTE ON NETWORK ACCESS: this fetches schema.org directly, which works fine
once deployed (Render has normal outbound internet access). It will fail
silently in network-restricted sandboxes -- that's intentional, see the
fallback behavior below. The curated GEO/AEO scoring in geo_rules.py does not
depend on this module, so a failed fetch degrades the tool gracefully rather
than breaking it.
"""

import requests

VOCAB_URL = "https://schema.org/version/latest/schemaorg-current-https.jsonld"
TIMEOUT = 10
DOCS_BASE = "https://schema.org/"

_cache = {"types": None, "properties": None}


def load_vocab():
    """Fetches and caches the set of known type names and property names.
    Returns (types, properties) -- both None if the fetch failed, so callers
    can skip this validation layer rather than crash."""
    if _cache["types"] is not None:
        return _cache["types"], _cache["properties"]

    try:
        resp = requests.get(VOCAB_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        graph = resp.json().get("@graph", [])
    except Exception:
        return None, None

    types = set()
    properties = set()
    for item in graph:
        item_id = item.get("@id", "")
        if not item_id.startswith("schema:"):
            continue
        name = item_id.split("schema:", 1)[1]
        item_type = item.get("@type", "")
        types_present = item_type if isinstance(item_type, list) else [item_type]
        if "rdfs:Class" in types_present:
            types.add(name)
        elif "rdf:Property" in types_present:
            properties.add(name)

    _cache["types"] = types
    _cache["properties"] = properties
    return types, properties


def docs_url_for(type_name):
    return f"{DOCS_BASE}{type_name}"

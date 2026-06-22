"""
scorer.py

Aggregates per-page structured data findings into one site-level report.

The score is a simple, transparent v1 heuristic -- not an industry-standard
metric. Treat the weighting below as a draft for you to adjust once you see
it run against a few real sites; you have the actual audit experience to
know whether 40/30/30 is the right split.
"""

import requests

from extractor import extract_json_ld, extract_microdata, get_types
from geo_rules import (
    AI_CRAWLER_USER_AGENTS,
    GEO_SIGNAL_TYPES,
    check_ai_crawler_access,
    check_required_fields,
)
from schema_vocab import docs_url_for, load_vocab

USER_AGENT = "GEOAuditBot/0.1 (+https://github.com/; site auditing tool)"
TIMEOUT = 10


def _check_llms_txt(domain):
    try:
        resp = requests.get(f"{domain.rstrip('/')}/llms.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _check_robots(domain):
    try:
        resp = requests.get(f"{domain.rstrip('/')}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return resp.text if resp.status_code == 200 else ""
    except requests.RequestException:
        return ""


def build_report(domain, crawl_results):
    known_types, _ = load_vocab()  # None, None if the live fetch failed

    page_reports = []
    pages_with_schema = 0
    geo_signal_count = 0

    for page in crawl_results:
        if page["error"] or not page["html"]:
            page_reports.append({
                "url": page["url"],
                "error": page["error"] or f"HTTP {page['status_code']}",
                "schema_items": [],
            })
            continue

        json_ld_items = extract_json_ld(page["html"])
        microdata_items = extract_microdata(page["html"], page["url"])

        entities = []
        for item in json_ld_items:
            for type_name in get_types(item):
                entities.append({"type": type_name, "format": "json-ld", "properties": item})
        for m in microdata_items:
            entities.append({"type": m["type"], "format": "microdata", "properties": m["properties"]})

        if entities:
            pages_with_schema += 1

        item_reports = []
        for entity in entities:
            type_name = entity["type"]
            missing_required, missing_recommended = check_required_fields(entity["properties"], type_name)
            is_recognized = (known_types is None) or (type_name in known_types)
            if type_name in GEO_SIGNAL_TYPES:
                geo_signal_count += 1
            item_reports.append({
                "type": type_name,
                "format": entity["format"],
                "recognized": is_recognized,
                "docs_url": docs_url_for(type_name),
                "missing_required": missing_required,
                "missing_recommended": missing_recommended,
            })

        page_reports.append({
            "url": page["url"],
            "error": None,
            "schema_items": item_reports,
        })

    total_pages = len(crawl_results)
    robots_txt = _check_robots(domain)
    blocked_crawlers = check_ai_crawler_access(robots_txt)
    llms_txt_present = _check_llms_txt(domain)

    schema_coverage_pct = (pages_with_schema / total_pages * 100) if total_pages else 0
    crawler_access_pct = (
        (len(AI_CRAWLER_USER_AGENTS) - len(blocked_crawlers)) / len(AI_CRAWLER_USER_AGENTS) * 100
    )
    geo_signal_score = min(geo_signal_count * 20, 100)  # crude: any GEO-type page is a strong positive signal

    overall_score = round(
        schema_coverage_pct * 0.4 + crawler_access_pct * 0.3 + geo_signal_score * 0.3
    )

    return {
        "domain": domain,
        "total_pages_scanned": total_pages,
        "pages_with_schema": pages_with_schema,
        "schema_coverage_pct": round(schema_coverage_pct),
        "blocked_ai_crawlers": blocked_crawlers,
        "llms_txt_present": llms_txt_present,
        "vocab_check_available": known_types is not None,
        "overall_score": overall_score,
        "pages": page_reports,
    }

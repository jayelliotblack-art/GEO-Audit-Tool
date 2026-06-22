"""
scorer.py

Aggregates per-page structured data findings into one site-level report.

The score is a simple, transparent v1 heuristic -- not an industry-standard
metric. Treat the weighting below as a draft for you to adjust once you see
it run against a few real sites; you have the actual audit experience to
know whether 40/30/30 is the right split.
"""

import requests
from urllib.parse import urlparse

from extractor import extract_json_ld, extract_microdata, get_types
from geo_rules import (
    AI_CRAWLER_USER_AGENTS,
    GEO_SIGNAL_TYPES,
    classify_ai_crawler_access,
    check_required_fields,
    is_fully_complete,
)
from schema_vocab import docs_url_for, load_vocab

USER_AGENT = "GEOAuditBot/0.1 (+https://github.com/; site auditing tool)"
TIMEOUT = 10


def _root(domain):
    parsed = urlparse(domain)
    return f"{parsed.scheme}://{parsed.netloc}"


def _check_llms_txt(domain):
    try:
        resp = requests.get(f"{_root(domain)}/llms.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _check_robots(domain):
    try:
        resp = requests.get(f"{_root(domain)}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return resp.text if resp.status_code == 200 else ""
    except requests.RequestException:
        return ""


def build_report(domain, crawl_results, sampled_urls):
    known_types, _ = load_vocab()  # None, None if the live fetch failed

    page_reports = []
    pages_with_schema = 0
    geo_signal_count = 0
    scoreable_total = 0
    scoreable_complete = 0

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
            complete = is_fully_complete(missing_required, missing_recommended, type_name)
            if complete is not None:
                scoreable_total += 1
                if complete:
                    scoreable_complete += 1
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
    ai_crawler_breakdown = classify_ai_crawler_access(robots_txt, sampled_urls)
    blocked_crawlers = [c["bot"] for c in ai_crawler_breakdown if not c["allowed"]]
    llms_txt_present = _check_llms_txt(domain)

    schema_coverage_pct = (pages_with_schema / total_pages * 100) if total_pages else 0

    total_bots = len(AI_CRAWLER_USER_AGENTS)
    crawler_access_pct = (total_bots - len(blocked_crawlers)) / total_bots * 100
    # Layer a small explicit/inherited modifier on top of the base allowed-
    # vs-blocked percentage above. Inherited (default-robots.txt) outcomes
    # get zero modifier either way -- a bot that happens to be allowed by a
    # wildcard it was never specifically considered for is fine, not
    # praiseworthy, and a bot blocked the same passive way is more likely an
    # oversight than a deliberate stance. Explicit outcomes get a real
    # modifier in both directions: a deliberate allow is a genuine GEO
    # decision worth a small reward; a deliberate block is a bigger,
    # 3x-weighted penalty, since actively shutting out AI crawlers is a more
    # consequential call than the low-effort act of allow-listing one.
    EXPLICIT_ALLOW_BONUS_MAX = 5
    EXPLICIT_BLOCK_PENALTY_MAX = 15
    explicit_allowed = sum(1 for c in ai_crawler_breakdown if c["allowed"] and c["explicit"])
    explicit_blocked = sum(1 for c in ai_crawler_breakdown if not c["allowed"] and c["explicit"])
    crawler_access_pct += (explicit_allowed / total_bots) * EXPLICIT_ALLOW_BONUS_MAX
    crawler_access_pct -= (explicit_blocked / total_bots) * EXPLICIT_BLOCK_PENALTY_MAX
    crawler_access_pct = max(0, min(100, crawler_access_pct))

    geo_signal_score = min(geo_signal_count * 20, 100)  # crude: any GEO-type page is a strong positive signal
    # % of detected entities (that we have a rule for) with no missing
    # required OR recommended fields. Defaults to 100 when nothing's
    # scoreable -- we don't penalize for types we have no opinion on.
    schema_quality_pct = (scoreable_complete / scoreable_total * 100) if scoreable_total else 100

    if total_pages == 0:
        # No pages were actually scanned (almost always: robots.txt disallowed
        # our crawler on every sampled URL). A score computed from zero data
        # is misleading, not just incomplete -- don't produce one.
        overall_score = None
    else:
        # Five pillars rather than three. llms.txt gets a light 10% rather
        # than being ignored or weighted heavily -- it's a real, displayed
        # finding (inconsistent to show it prominently and let it affect
        # nothing), but it's still an emerging, unofficial convention, and a
        # genuinely well-optimized site shouldn't be punished hard for not
        # having adopted something unproven yet. These weights are exactly
        # the kind of call that should run through your judgment, not mine --
        # adjust freely once you've seen this run against more real sites.
        llms_score = 100 if llms_txt_present else 0
        overall_score = round(
            schema_coverage_pct * 0.25
            + schema_quality_pct * 0.25
            + crawler_access_pct * 0.20
            + geo_signal_score * 0.20
            + llms_score * 0.10
        )

    return {
        "domain": domain,
        "total_pages_scanned": total_pages,
        "pages_with_schema": pages_with_schema,
        "schema_coverage_pct": round(schema_coverage_pct),
        "schema_quality_pct": round(schema_quality_pct),
        "scoreable_total": scoreable_total,
        "scoreable_complete": scoreable_complete,
        "blocked_ai_crawlers": blocked_crawlers,
        "ai_crawler_breakdown": ai_crawler_breakdown,
        "crawler_access_pct": round(crawler_access_pct),
        "llms_txt_present": llms_txt_present,
        "vocab_check_available": known_types is not None,
        "overall_score": overall_score,
        "pages": page_reports,
    }

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

from extractor import (
    extract_canonical,
    extract_internal_links,
    extract_json_ld,
    extract_meta_robots,
    extract_microdata,
    extract_visible_text,
    get_types,
    is_noindexed,
    parse_html,
)
from geo_rules import (
    AI_CRAWLER_USER_AGENTS,
    GEO_SIGNAL_TYPES,
    assess_freshness,
    classify_ai_crawler_access,
    classify_canonical,
    check_required_fields,
    check_schema_truthfulness,
    grade_llms_txt,
    is_fully_complete,
    item_completeness_pct,
)
from schema_vocab import docs_url_for, load_vocab

USER_AGENT = "GEOAuditBot/0.1 (+https://github.com/; site auditing tool)"
TIMEOUT = 10


def _root(domain):
    parsed = urlparse(domain)
    return f"{parsed.scheme}://{parsed.netloc}"


def _fetch_llms_txt(domain):
    """Returns (present, content). content is '' if absent or unreachable."""
    try:
        resp = requests.get(f"{_root(domain)}/llms.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if resp.status_code == 200:
            return True, resp.text
    except requests.RequestException:
        pass
    return False, ""


def _check_robots(domain):
    try:
        resp = requests.get(f"{_root(domain)}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        return resp.text if resp.status_code == 200 else ""
    except requests.RequestException:
        return ""


def build_report(domain, crawl_results, sampled_urls, lastmod_by_url=None, urls_found_total=None):
    known_types, _ = load_vocab()  # None, None if the live fetch failed
    lastmod_by_url = lastmod_by_url or {}

    # Pass 1: extract everything per-page, including canonical hrefs (not
    # yet classified) and a url_health map -- canonical classification needs
    # to know whether the TARGET page is noindexed/broken, which we can only
    # know once every page in this scan has been processed once.
    page_data = []
    url_health = {}
    linked_urls = set()
    pages_with_schema = 0
    geo_signal_count = 0
    scoreable_total = 0
    scoreable_complete = 0
    weighted_completeness_sum = 0.0
    weighted_completeness_count = 0
    noindexed_count = 0
    required_issues = 0
    recommended_issues = 0
    unrecognized_issues = 0
    truthfulness_issue_count = 0
    truthfulness_flagged_urls = []

    for page in crawl_results:
        if page["error"] or not page["html"]:
            page_data.append({
                "url": page["url"],
                "error": page["error"] or f"HTTP {page['status_code']}",
                "schema_items": [],
                "noindexed": False,
                "canonical_urls": [],
            })
            url_health[page["url"].rstrip("/")] = {"noindexed": False, "error": page_data[-1]["error"]}
            continue

        soup = parse_html(page["html"])
        meta_robots = extract_meta_robots(soup)
        noindexed = is_noindexed(meta_robots)
        if noindexed:
            noindexed_count += 1
        canonical_urls = extract_canonical(soup, page["url"])
        url_health[page["url"].rstrip("/")] = {"noindexed": noindexed, "error": None}

        page_url_norm = page["url"].rstrip("/")
        page_links = extract_internal_links(soup, page["url"])
        linked_urls.update(page_links - {page_url_norm})  # a page linking to itself shouldn't un-orphan it

        json_ld_items = extract_json_ld(soup)
        microdata_items = extract_microdata(page["html"], page["url"])

        entities = []
        for item in json_ld_items:
            for type_name in get_types(item):
                entities.append({"type": type_name, "format": "json-ld", "properties": item})
        for m in microdata_items:
            entities.append({"type": m["type"], "format": "microdata", "properties": m["properties"]})

        # extract_visible_text walks every text node in the page -- real cost
        # on a long blog post or case-study page. check_schema_truthfulness
        # only ever consumes it for FAQPage/HowTo, so skip the walk entirely
        # for every other page rather than paying for it unconditionally.
        needs_visible_text = any(e["type"] in ("FAQPage", "HowTo") for e in entities)
        visible_text = extract_visible_text(soup) if needs_visible_text else ""

        if entities:
            pages_with_schema += 1

        page_flagged_for_truthfulness = False
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

            weighted_pct = item_completeness_pct(missing_required, missing_recommended, type_name)
            if weighted_pct is not None:
                weighted_completeness_sum += weighted_pct
                weighted_completeness_count += 1

            required_issues += len(missing_required)
            recommended_issues += len(missing_recommended)
            if not is_recognized:
                unrecognized_issues += 1

            truthfulness = check_schema_truthfulness(entity["properties"], type_name, visible_text)
            content_mismatch = None
            if truthfulness is not None:
                mismatches, total_claims = truthfulness
                content_mismatch = {"mismatches": mismatches, "total": total_claims}
                if mismatches / total_claims >= 0.5:
                    page_flagged_for_truthfulness = True

            item_reports.append({
                "type": type_name,
                "format": entity["format"],
                "recognized": is_recognized,
                "docs_url": docs_url_for(type_name),
                "missing_required": missing_required,
                "missing_recommended": missing_recommended,
                "content_mismatch": content_mismatch,
            })

        if page_flagged_for_truthfulness:
            truthfulness_issue_count += 1
            truthfulness_flagged_urls.append(page["url"])

        page_data.append({
            "url": page["url"],
            "error": None,
            "schema_items": item_reports,
            "noindexed": noindexed,
            "canonical_urls": canonical_urls,
        })

        # Nothing downstream (Pass 2, the template, the PDF/summary export)
        # ever needs the raw HTML again -- only the small derived data above.
        # Drop the reference now rather than holding every page's full body
        # in memory for the rest of the request; with 100 pages in flight,
        # that's the difference between releasing ~1 page of HTML at a time
        # versus all ~100 simultaneously.
        page["html"] = None

    # Pass 2: classify each page's canonical now that url_health is complete,
    # and check orphan status now that linked_urls is complete (both need
    # every page processed once before they can be evaluated).
    page_reports = []
    canonical_issue_count = 0
    orphan_count = 0
    for pd in page_data:
        canonical = classify_canonical(pd["url"], pd["canonical_urls"], url_health) if not pd["error"] else None
        if canonical and canonical["status"] in ("multiple", "cross_domain"):
            canonical_issue_count += 1
        elif canonical and canonical["status"] == "other_page" and canonical["target_health"] and (
            canonical["target_health"]["noindexed"] or canonical["target_health"]["error"]
        ):
            canonical_issue_count += 1

        is_orphan = (not pd["error"]) and (pd["url"].rstrip("/") not in linked_urls)
        if is_orphan:
            orphan_count += 1

        page_reports.append({
            "url": pd["url"],
            "error": pd["error"],
            "schema_items": pd["schema_items"],
            "noindexed": pd["noindexed"],
            "canonical": canonical,
            "is_orphan": is_orphan,
        })

    total_pages = len(page_data)
    robots_txt = _check_robots(domain)
    ai_crawler_breakdown = classify_ai_crawler_access(robots_txt, sampled_urls)
    blocked_crawlers = [c["bot"] for c in ai_crawler_breakdown if not c["allowed"]]
    llms_txt_present, llms_txt_content = _fetch_llms_txt(domain)
    llms_txt_quality_pct, llms_txt_notes = grade_llms_txt(llms_txt_content) if llms_txt_present else (0, [])

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
    # The score that actually feeds overall_score: a weighted average where
    # missing required fields cost full weight and missing recommended ones
    # cost RECOMMENDED_WEIGHT as much (see geo_rules.item_completeness_pct).
    # Same three-case structure as before for *why* this might be empty:
    #   - schema exists, but none of it is a type we have a rule for -> no
    #     basis to penalize, default to 100 (benefit of the doubt)
    #   - no schema exists on the site at all -> there's nothing to BE
    #     complete, defaulting to 100 here would claim "perfect" for a site
    #     with literally zero structured data, which is the opposite of true
    if weighted_completeness_count:
        schema_quality_pct = weighted_completeness_sum / weighted_completeness_count
    elif pages_with_schema:
        schema_quality_pct = 100
    else:
        schema_quality_pct = 0

    sample_coverage_pct = (
        round(total_pages / urls_found_total * 100) if urls_found_total else None
    )

    if total_pages == 0:
        # No pages were actually scanned (almost always: robots.txt disallowed
        # our crawler on every sampled URL). A score computed from zero data
        # is misleading, not just incomplete -- don't produce one.
        overall_score = None
        noindex_score = None
        freshness_pct = None
        freshness_median_age = None
        freshness_notes = []
        orphan_penalty = 0
        truthfulness_penalty = 0
        canonical_penalty = 0
    else:
        # noindex_score: % of scanned pages NOT told to stay out of the
        # index. A noindexed page is invisible to engines regardless of how
        # good its schema is -- arguably more severe than incomplete schema,
        # so it gets real weight rather than a token one.
        noindex_score = (total_pages - noindexed_count) / total_pages * 100

        relevant_lastmod = {u: lastmod_by_url.get(u) for u in sampled_urls}
        freshness_pct, freshness_median_age, freshness_notes = assess_freshness(relevant_lastmod)

        # Seven pillars now. Schema coverage + quality stay the biggest
        # share -- schema markup is this tool's actual centerpiece, not an
        # equal slice among others. Freshness gets deliberately less than
        # the other four supporting signals: lastmod is a noisier, less
        # trustworthy signal in practice (see assess_freshness), so even
        # though it's real, it shouldn't move the score as hard as something
        # unambiguous like noindex. Trimmed evenly off the other six to make
        # room for a genuinely "mild" 4% rather than bolting it on top.
        base_score = round(
            schema_coverage_pct * 0.24
            + schema_quality_pct * 0.24
            + noindex_score * 0.12
            + crawler_access_pct * 0.12
            + geo_signal_score * 0.12
            + llms_txt_quality_pct * 0.12
            + freshness_pct * 0.04
        )

        # Flat per-instance penalties on top of the weighted base, rather
        # than folding these into the percentage pillars above -- each is a
        # countable, concrete finding (a specific page, a specific entity)
        # rather than a site-wide rate, so a flat deduction per occurrence
        # reads more honestly than forcing it into a 0-100 percentage.
        # Each is capped so one noisy category can't swamp the whole score.

        # Orphan pages: only scored when sample_coverage_pct == 100, mirroring
        # the display gate exactly. The reasoning is the same as for hiding
        # it on a partial sample -- below 100% coverage, an "orphan" usually
        # just means the linking page wasn't sampled, not that the page is
        # truly unlinked. Scoring an unreliable signal would be worse than
        # not showing it at all.
        orphan_penalty = min(orphan_count, 15) if sample_coverage_pct == 100 else 0

        # Schema truthfulness: unlike orphans, this doesn't need full-site
        # visibility to be reliable -- each flagged entity was checked
        # against its own page's actual content, independent of sample size.
        # Weighted higher per instance (2 vs 1) since markup asserting
        # content that isn't there is arguably worse for AEO trust than a
        # missing field would be.
        truthfulness_penalty = min(truthfulness_issue_count * 2, 20)

        # Canonical issues: real, but lower-confidence on average than the
        # other two -- canonical_issue_count mixes a clearly-broken case
        # (multiple canonical tags), an ambiguous one (cross-domain, which is
        # sometimes a deliberate syndication choice, not a mistake), and a
        # serious one (canonical pointing at a noindexed/broken page) into a
        # single count. Given that mix, a smaller per-instance weight and a
        # lower cap than truthfulness feels right until this has been
        # checked against more real sites to argue for splitting the
        # sub-cases out and weighting them individually instead.
        canonical_penalty = min(canonical_issue_count, 10)

        overall_score = max(0, base_score - orphan_penalty - truthfulness_penalty - canonical_penalty)

    total_issues = required_issues + recommended_issues + unrecognized_issues

    return {
        "domain": domain,
        "root_domain": _root(domain),
        "sample_coverage_pct": sample_coverage_pct,
        "total_pages_scanned": total_pages,
        "pages_with_schema": pages_with_schema,
        "schema_coverage_pct": round(schema_coverage_pct),
        "schema_quality_pct": round(schema_quality_pct),
        "scoreable_total": scoreable_total,
        "scoreable_complete": scoreable_complete,
        "total_issues": total_issues,
        "required_issues": required_issues,
        "recommended_issues": recommended_issues,
        "unrecognized_issues": unrecognized_issues,
        "noindexed_count": noindexed_count,
        "noindex_score": round(noindex_score) if noindex_score is not None else None,
        "canonical_issue_count": canonical_issue_count,
        "canonical_penalty": canonical_penalty,
        "orphan_count": orphan_count,
        "orphan_penalty": orphan_penalty,
        "truthfulness_issue_count": truthfulness_issue_count,
        "truthfulness_penalty": truthfulness_penalty,
        "truthfulness_flagged_urls": truthfulness_flagged_urls,
        "freshness_pct": freshness_pct,
        "freshness_median_age_days": freshness_median_age,
        "freshness_notes": freshness_notes,
        "blocked_ai_crawlers": blocked_crawlers,
        "ai_crawler_breakdown": ai_crawler_breakdown,
        "crawler_access_pct": round(crawler_access_pct),
        "llms_txt_present": llms_txt_present,
        "llms_txt_quality_pct": round(llms_txt_quality_pct),
        "llms_txt_notes": llms_txt_notes,
        "vocab_check_available": known_types is not None,
        "overall_score": overall_score,
        "pages": page_reports,
    }

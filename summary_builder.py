"""
summary_builder.py

Builds an "executive summary" and a cross-category issues list purely by
selecting and assembling pre-written sentence fragments based on which
findings are present in the report -- no LLM call, no tokens, no per-scan
cost. Every sentence is hand-written here; the only thing chosen at
runtime is WHICH sentences apply and in what order.

Ranking is by points actually lost, on ONE consistent scale, which is the
part worth understanding before touching this file. Two different kinds
of "issue" exist in this codebase:
  - Flat penalties (heading structure, malformed JSON-LD, orphans,
    truthfulness, canonical issues) already carry a real points-lost value
    computed in scorer.py.
  - Percentage PILLARS (schema coverage, schema quality, crawler access,
    noindex, freshness, llms.txt) don't have a "penalty" field at all --
    they're a percentage that gets multiplied by a weight inside
    base_score. A pillar sitting at 0% is costing its FULL weight, which
    can easily be the single biggest problem in the whole report, but
    nothing about a bare percentage says so on its own.
This module converts every pillar shortfall into the same "points lost"
units as the flat penalties (via SCORE_WEIGHTS, shared with scorer.py so
the two can't drift apart) specifically so ranking "what's actually hurting
the score the most" compares like with like. A site with 0% schema
coverage AND 0% schema quality should -- and now does -- outrank a heading
issue worth a couple of points, instead of never being eligible to be
"the most significant factor" at all.
"""

from geo_rules import SCORE_WEIGHTS


def _s(n):
    return "" if n == 1 else "s"


def _is_are(n):
    return "is" if n == 1 else "are"


def _has_have(n):
    return "has" if n == 1 else "have"


def _pillar_points_lost(pct, weight):
    """Points a percentage pillar is costing relative to a perfect 100% on
    that pillar -- e.g. at 0% it costs its full weight; at 90% it only
    costs a tenth of it. Returns 0 if the pillar has no value to assess
    (e.g. freshness with no usable lastmod data)."""
    if pct is None:
        return 0
    return (100 - pct) * weight


def build_issue_records(report):
    """Returns a list of {"key", "label", "points", "note"} -- one entry
    per category that's actually costing something, in no particular order
    (callers sort as needed). Every record's "points" is on the same
    points-lost scale, whether the category is a flat penalty or a
    percentage pillar -- see module docstring."""
    records = []

    # --- Schema coverage: how much of the site has ANY structured data at
    # all. This is deliberately separate from schema QUALITY below -- a
    # site can have excellent coverage with mediocre quality, or vice
    # versa, and conflating them would hide which one to actually fix.
    coverage_pct = report.get("schema_coverage_pct", 0)
    coverage_points = _pillar_points_lost(coverage_pct, SCORE_WEIGHTS["schema_coverage"])
    if coverage_points >= 1:
        total_pages = report.get("total_pages_scanned", 0)
        with_schema = report.get("pages_with_schema", 0)
        without = total_pages - with_schema
        records.append({
            "key": "schema_coverage", "label": "Schema coverage",
            "points": round(coverage_points, 1),
            "note": f"Only {coverage_pct}% of scanned pages have any structured data at all ({without} of {total_pages} page{_s(total_pages)} with none).",
        })

    # --- Schema quality: among whatever schema DOES exist, how complete is
    # it. When there's no schema at all, quality_pct is 0 by definition
    # (see geo_rules) -- the note says so plainly rather than implying
    # there's a completeness problem to fix when the real problem is
    # absence, which the coverage record above already names.
    quality_pct = report.get("schema_quality_pct", 0)
    quality_points = _pillar_points_lost(quality_pct, SCORE_WEIGHTS["schema_quality"])
    required = report.get("required_issues", 0)
    recommended = report.get("recommended_issues", 0)
    unrecognized = report.get("unrecognized_issues", 0)
    if quality_points >= 1:
        if required or recommended or unrecognized:
            parts = []
            if required:
                parts.append(f"{required} missing required field{_s(required)}")
            if recommended:
                parts.append(f"{recommended} missing recommended field{_s(recommended)}")
            if unrecognized:
                parts.append(f"{unrecognized} unrecognized schema type{_s(unrecognized)}")
            note = ", ".join(parts) + " across the scanned pages."
        else:
            note = "There's no structured data on the scanned pages to assess completeness against in the first place."
        records.append({"key": "schema_quality", "label": "Schema quality", "points": round(quality_points, 1), "note": note})

    malformed = report.get("malformed_jsonld_pages", 0)
    if malformed:
        records.append({
            "key": "malformed_jsonld", "label": "Malformed JSON-LD",
            "points": report.get("malformed_jsonld_penalty", 0),
            "note": f"{malformed} page{_s(malformed)} {_has_have(malformed)} structured data that fails to parse as valid JSON-LD, providing zero value to anything reading it.",
        })

    # --- AI crawler access: a pillar, not just a flat "were any bots
    # explicitly blocked" check -- a site can lose points here from
    # INHERITED robots.txt rules too, with no explicit block in sight.
    crawler_pct = report.get("crawler_access_pct", 0)
    crawler_points = _pillar_points_lost(crawler_pct, SCORE_WEIGHTS["crawler_access"])
    blocked = report.get("blocked_ai_crawlers") or []
    if crawler_points >= 1:
        if blocked:
            note = f"{len(blocked)} AI crawler{_s(len(blocked))} {_is_are(len(blocked))} explicitly blocked: {', '.join(blocked)}."
        else:
            note = f"AI crawler access is only {round(crawler_pct)}% clear even without any explicit blocks -- likely an inherited robots.txt rule catching them by accident."
        records.append({"key": "crawler_access", "label": "AI crawler access", "points": round(crawler_points, 1), "note": note})

    # --- Noindex: also a pillar (noindex_score), not just a bare count.
    noindex_score = report.get("noindex_score")
    noindex_points = _pillar_points_lost(noindex_score, SCORE_WEIGHTS["noindex"])
    noindexed = report.get("noindexed_count", 0)
    if noindex_points >= 1:
        records.append({
            "key": "noindex", "label": "Noindexed pages",
            "points": round(noindex_points, 1),
            "note": f"{noindexed} page{_s(noindexed)} {_has_have(noindexed)} a noindex directive, making {'it' if noindexed == 1 else 'them'} invisible to engines regardless of schema quality.",
        })

    canonical_count = report.get("canonical_issue_count", 0)
    if canonical_count:
        records.append({
            "key": "canonical", "label": "Canonical issues",
            "points": report.get("canonical_penalty", 0),
            "note": f"{canonical_count} canonical tag issue{_s(canonical_count)} found -- duplicate tags, cross-domain targets, or a canonical pointing at a broken/noindexed page.",
        })

    orphan_count = report.get("orphan_count", 0)
    if orphan_count and report.get("sample_coverage_pct") == 100:
        records.append({
            "key": "orphan", "label": "Orphan pages",
            "points": report.get("orphan_penalty", 0),
            "note": f"{orphan_count} page{_s(orphan_count)} {_has_have(orphan_count)} no inbound links from anywhere else on the site.",
        })

    truthfulness_count = report.get("truthfulness_issue_count", 0)
    if truthfulness_count:
        records.append({
            "key": "truthfulness", "label": "Schema truthfulness",
            "points": report.get("truthfulness_penalty", 0),
            "note": f"{truthfulness_count} piece{_s(truthfulness_count)} of schema {'makes' if truthfulness_count == 1 else 'make'} a claim that doesn't match the page's actual visible content.",
        })

    heading_count = report.get("heading_issue_pages", 0)
    if heading_count:
        records.append({
            "key": "heading", "label": "Heading structure",
            "points": report.get("heading_penalty", 0),
            "note": f"{heading_count} page{_s(heading_count)} {_has_have(heading_count)} an unclear heading outline ({report.get('pages_missing_h1', 0)} missing an H1, {report.get('pages_multiple_h1', 0)} with more than one, {report.get('pages_heading_skips', 0)} with a level skip).",
        })

    thin_count = report.get("thin_content_pages", 0)
    if thin_count:
        records.append({
            "key": "thin_content", "label": "Thin / JS-rendered content",
            "points": report.get("thin_content_penalty", 0),
            "note": f"{thin_count} page{_s(thin_count)} may rely on JavaScript rendering that most AI crawlers won't execute.",
        })

    # --- Freshness: a pillar too, but only worth mentioning when we
    # actually have a confident reading -- assess_freshness already
    # defaults to 100 (no penalty) when lastmod data is missing or looks
    # unreliable, so a low freshness_pct here always reflects real data.
    freshness_pct = report.get("freshness_pct")
    freshness_points = _pillar_points_lost(freshness_pct, SCORE_WEIGHTS["freshness"])
    if freshness_points >= 1 and report.get("freshness_median_age_days") is not None:
        records.append({
            "key": "freshness", "label": "Content freshness",
            "points": round(freshness_points, 1),
            "note": f"Median content age across sampled pages is {report.get('freshness_median_age_days')} days -- meaningfully stale.",
        })

    # --- GEO-specific schema signals: a real pillar in the score formula
    # with no dedicated stat card of its own, since it's really a special
    # case of schema coverage (FAQPage, HowTo, etc. specifically). Still
    # worth surfacing here if it's a meaningful drag, even without its own
    # card in the dashboard above.
    geo_signal_points = _pillar_points_lost(report.get("geo_signal_score"), SCORE_WEIGHTS["geo_signal"])
    if geo_signal_points >= 1:
        records.append({
            "key": "geo_signal", "label": "GEO-specific schema",
            "points": round(geo_signal_points, 1),
            "note": "Few or no GEO-specific schema types (FAQPage, HowTo, and similar) were found across the scanned pages.",
        })

    llms_pct = report.get("llms_txt_quality_pct", 0)
    llms_points = _pillar_points_lost(llms_pct, SCORE_WEIGHTS["llms_txt"])
    if llms_points >= 1:
        if not report.get("llms_txt_present"):
            note = "No llms.txt file was found at the site root."
        else:
            note = f"llms.txt is present, but graded at only {llms_pct}% quality."
        records.append({"key": "llms_txt", "label": "llms.txt", "points": round(llms_points, 1), "note": note})

    return records


def _clean_domain(domain):
    """Strips the protocol and a leading www. for natural reading in prose
    -- https://www.betterup.com becomes betterup.com. The report header
    elsewhere still shows the full root_domain on purpose; this is specific
    to how a domain reads inside a sentence."""
    cleaned = domain.replace("https://", "").replace("http://", "")
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned


def _score_opening(score, domain):
    domain = _clean_domain(domain)
    if score >= 85:
        return f"{domain} scores {score}/100 for AI search readiness -- a strong result that puts it well ahead of most sites we've audited."
    if score >= 70:
        return f"{domain} scores {score}/100 for AI search readiness, a solid result with a handful of specific gaps worth closing."
    if score >= 50:
        return f"{domain} scores {score}/100 for AI search readiness -- a workable foundation, but several issues are likely limiting how reliably AI systems can read and cite this site."
    if score >= 30:
        return f"{domain} scores {score}/100 for AI search readiness, which signals real structural problems likely costing this site visibility in AI-generated answers."
    return f"{domain} scores {score}/100 for AI search readiness -- among the more difficult results to see, with foundational issues across multiple categories."


def build_executive_summary(report):
    """Returns a list of paragraph strings (no markup), assembled entirely
    from fragments based on which issue records apply -- deterministic,
    no LLM call. Leads with the score and the single biggest point-cost
    issue (pillar or flat penalty, ranked on the same scale -- see module
    docstring), mentions up to two more, and closes with a genuine
    strength if one exists so this doesn't read as purely a complaint
    list."""
    if report.get("overall_score") is None:
        return []

    records = build_issue_records(report)
    scored = sorted([r for r in records if r["points"] > 0], key=lambda r: -r["points"])

    paragraphs = [_score_opening(report["overall_score"], report["root_domain"])]

    top_two_keys = {r["key"] for r in scored[:2]}
    if len(scored) >= 2 and top_two_keys == {"schema_coverage", "schema_quality"}:
        # Both schema pillars topping the list together is almost always
        # one root cause wearing two hats -- essentially no usable schema
        # markup on the site -- and reads far more clearly as one combined
        # statement than as two sentences each claiming to be "the" biggest
        # factor.
        para = (
            "The most significant factor pulling the score down is structured data: "
            "there's effectively no usable schema markup on the scanned pages, which costs heavily "
            "on both coverage and quality at once."
        )
        remaining = scored[2:4]
        if remaining:
            para += " " + " ".join(r["note"] for r in remaining)
        paragraphs.append(para)
    elif scored:
        top = scored[0]
        para = f"The most significant factor pulling the score down is {top['label'].lower()}: {top['note']}"
        if len(scored) > 1:
            para += " " + " ".join(r["note"] for r in scored[1:3])
        paragraphs.append(para)
    elif records:
        paragraphs.append(" ".join(r["note"] for r in records[:3]))
    else:
        paragraphs.append("No significant issues were found across any category in this scan -- an unusually clean result.")

    strengths = []
    if report.get("schema_coverage_pct", 0) >= 90 and report.get("schema_quality_pct", 0) >= 90:
        strengths.append("structured data coverage and quality are both excellent")
    if report.get("crawler_access_pct", 0) >= 95 and not report.get("blocked_ai_crawlers"):
        strengths.append("every major AI crawler has clear, unblocked access")
    if report.get("llms_txt_present") and report.get("llms_txt_quality_pct", 0) >= 80:
        strengths.append("llms.txt is in genuinely good shape")
    if strengths:
        paragraphs[-1] += " On the positive side, " + " and ".join(strengths) + "."

    return paragraphs

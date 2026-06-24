"""
summary_builder.py

Builds an "executive summary" and a cross-category issues list purely by
selecting and assembling pre-written sentence fragments based on which
findings are present in the report -- no LLM call, no tokens, no per-scan
cost. Every sentence is hand-written here; the only thing chosen at
runtime is WHICH sentences apply and in what order (ranked by how many
points each issue actually costs, biggest first).

This is deliberately a library of fragments, not a paragraph generator --
keep new fragments here as new checks are added, rather than building
generation logic that tries to be clever about phrasing.
"""


def _s(n):
    return "" if n == 1 else "s"


def _is_are(n):
    return "is" if n == 1 else "are"


def _has_have(n):
    return "has" if n == 1 else "have"


def build_issue_records(report):
    """Returns a list of {"key", "label", "points", "note"} -- one entry
    per category that actually has something worth surfacing, in no
    particular order (callers sort as needed). "points" is the score
    penalty that category caused, or 0 if it's not a flat penalty (e.g.
    schema completeness feeds a percentage pillar instead)."""
    records = []

    required = report.get("required_issues", 0)
    recommended = report.get("recommended_issues", 0)
    unrecognized = report.get("unrecognized_issues", 0)
    if required or recommended or unrecognized:
        parts = []
        if required:
            parts.append(f"{required} missing required field{_s(required)}")
        if recommended:
            parts.append(f"{recommended} missing recommended field{_s(recommended)}")
        if unrecognized:
            parts.append(f"{unrecognized} unrecognized schema type{_s(unrecognized)}")
        records.append({
            "key": "schema_quality", "label": "Schema completeness", "points": 0,
            "note": ", ".join(parts) + " across the scanned pages.",
        })

    malformed = report.get("malformed_jsonld_pages", 0)
    if malformed:
        records.append({
            "key": "malformed_jsonld", "label": "Malformed JSON-LD",
            "points": report.get("malformed_jsonld_penalty", 0),
            "note": f"{malformed} page{_s(malformed)} {_has_have(malformed)} structured data that fails to parse as valid JSON-LD, providing zero value to anything reading it.",
        })

    blocked = report.get("blocked_ai_crawlers") or []
    if blocked:
        records.append({
            "key": "crawler_access", "label": "AI crawler access", "points": 0,
            "note": f"{len(blocked)} AI crawler{_s(len(blocked))} {_is_are(len(blocked))} explicitly blocked: {', '.join(blocked)}.",
        })

    noindexed = report.get("noindexed_count", 0)
    if noindexed:
        records.append({
            "key": "noindex", "label": "Noindexed pages", "points": 0,
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

    freshness_pct = report.get("freshness_pct")
    if freshness_pct is not None and freshness_pct < 70:
        records.append({
            "key": "freshness", "label": "Content freshness", "points": 0,
            "note": f"Median content age across sampled pages is {report.get('freshness_median_age_days')} days -- meaningfully stale.",
        })

    if not report.get("llms_txt_present"):
        records.append({
            "key": "llms_txt", "label": "llms.txt", "points": 0,
            "note": "No llms.txt file was found at the site root.",
        })
    elif report.get("llms_txt_quality_pct", 0) < 70:
        records.append({
            "key": "llms_txt", "label": "llms.txt", "points": 0,
            "note": f"llms.txt is present, but graded at only {report['llms_txt_quality_pct']}% quality.",
        })

    return records


def _score_opening(score, domain):
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
    issue, mentions up to two more, and closes with a genuine strength if
    one exists so this doesn't read as purely a complaint list."""
    if report.get("overall_score") is None:
        return []

    records = build_issue_records(report)
    scored = sorted([r for r in records if r["points"] > 0], key=lambda r: -r["points"])

    paragraphs = [_score_opening(report["overall_score"], report["root_domain"])]

    if scored:
        top = scored[0]
        para = f"The most significant factor pulling the score down is {top['label'].lower()}: {top['note']}"
        if len(scored) > 1:
            para += " " + " ".join(r["note"] for r in scored[1:3])
        paragraphs.append(para)
    elif records:
        # Issues exist but none carry a flat point penalty (e.g. only
        # schema-completeness gaps, which feed a percentage pillar instead).
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

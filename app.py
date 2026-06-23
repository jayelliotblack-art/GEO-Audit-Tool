"""
app.py

Entry point. Two routes: a form to enter a domain, and a results page that
runs the actual scan.

v1 deliberately runs synchronously and caps the number of pages scanned --
see MAX_URLS below. That keeps the architecture simple (no job queue, no
websockets) at the cost of a slower response on large sites. Worth revisiting
once the core checks are proven out.
"""

import json
import re
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request

from crawler import fetch_pages
from pdf_export import generate_pdf
from sitemap import get_sitemap_urls
from scorer import build_report
from summarizer import generate_summary

app = Flask(__name__)
app.jinja_env.filters["urlpath"] = lambda u: urlparse(u).path or "/"

MAX_URLS = 100  # rolled back from 150 -- that increase, stacked on top of an
# unproven fix, led directly to a second OOM. Holding here until the
# text-node-limit fix in extract_visible_text has real evidence behind it
# before trying to raise this again.


def _normalize_domain(raw):
    raw = raw.strip()
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    return raw.rstrip("/")


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/scan", methods=["POST"])
def scan():
    raw_domain = request.form.get("domain", "")
    if not raw_domain:
        return render_template("index.html", error="Enter a domain to scan.")

    domain = _normalize_domain(raw_domain)

    urls, total_found, lastmod_by_url, sitemap_error = get_sitemap_urls(domain)
    if sitemap_error:
        return render_template("index.html", error=sitemap_error)

    if not urls:
        return render_template("index.html", error=f"No URLs found in {domain}'s sitemap.")

    urls_to_scan = urls[:MAX_URLS]
    crawl_results, skipped_by_robots, robots_access_denied = fetch_pages(urls_to_scan, domain)
    report = build_report(domain, crawl_results, urls_to_scan, lastmod_by_url)
    report["urls_found_total"] = total_found
    report["skipped_by_robots"] = skipped_by_robots
    # % of the site actually sampled -- orphan detection in particular is
    # only as good as this number. Sampling 100 of 2,800 pages means most
    # "orphan" flags are just "the linking page wasn't in the sample," not a
    # real finding; this lets the report say so honestly rather than
    # presenting a low-confidence number with the same confidence as
    # everything else.
    report["sample_coverage_pct"] = (
        round(report["total_pages_scanned"] / total_found * 100) if total_found else None
    )
    report["robots_access_denied"] = robots_access_denied

    # Trimmed subset sent to the optional AI-summary button -- aggregate
    # stats only, not every page/URL, to keep the prompt (and its cost) small.
    summary_data = {
        "domain": report["root_domain"],
        "overall_score": report["overall_score"],
        "schema_coverage_pct": report["schema_coverage_pct"],
        "pages_with_schema": report["pages_with_schema"],
        "total_pages_scanned": report["total_pages_scanned"],
        "schema_quality_pct": report["schema_quality_pct"],
        "scoreable_complete": report["scoreable_complete"],
        "scoreable_total": report["scoreable_total"],
        "crawler_access_pct": report["crawler_access_pct"],
        "blocked_ai_crawlers": report["blocked_ai_crawlers"],
        "ai_crawler_breakdown": report["ai_crawler_breakdown"],
        "llms_txt_present": report["llms_txt_present"],
        "noindexed_count": report["noindexed_count"],
        "canonical_issue_count": report["canonical_issue_count"],
        "freshness_pct": report["freshness_pct"],
        "freshness_median_age_days": report["freshness_median_age_days"],
    }

    return render_template("report.html", report=report, summary_data=summary_data)


@app.route("/summarize", methods=["POST"])
def summarize():
    data = request.get_json(silent=True) or {}
    summary, error = generate_summary(data)
    if error:
        return jsonify({"error": error}), 502
    return jsonify({"summary": summary})


@app.route("/download-pdf", methods=["POST"])
def download_pdf():
    try:
        report = json.loads(request.form.get("report_json", "{}"))
    except json.JSONDecodeError:
        return "Invalid report data", 400

    pdf_bytes = generate_pdf(report)

    domain = report.get("root_domain") or report.get("domain", "report")
    safe_name = re.sub(r"[^a-zA-Z0-9.-]+", "-", domain.replace("https://", "").replace("http://", "")).strip("-")
    filename = f"geo-audit-{safe_name or 'report'}.pdf"

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)

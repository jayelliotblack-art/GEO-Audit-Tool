"""
app.py

Entry point. Two routes: a form to enter a domain, and a results page that
runs the actual scan.

v1 deliberately runs synchronously and caps the number of pages scanned --
see MAX_URLS below. That keeps the architecture simple (no job queue, no
websockets) at the cost of a slower response on large sites. Worth revisiting
once the core checks are proven out.
"""

from flask import Flask, jsonify, render_template, request

from crawler import fetch_pages
from sitemap import get_sitemap_urls
from scorer import build_report
from summarizer import generate_summary

app = Flask(__name__)

MAX_URLS = 50  # raised from 25 now that the pipeline's proven against real sites;
# this needs gunicorn's timeout raised to match -- see README


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

    urls, total_found, sitemap_error = get_sitemap_urls(domain)
    if sitemap_error:
        return render_template("index.html", error=sitemap_error)

    if not urls:
        return render_template("index.html", error=f"No URLs found in {domain}'s sitemap.")

    urls_to_scan = urls[:MAX_URLS]
    crawl_results, skipped_by_robots, robots_access_denied = fetch_pages(urls_to_scan, domain)
    report = build_report(domain, crawl_results, urls_to_scan)
    report["urls_found_total"] = total_found
    report["skipped_by_robots"] = skipped_by_robots
    report["robots_access_denied"] = robots_access_denied

    # Trimmed subset sent to the optional AI-summary button -- aggregate
    # stats only, not every page/URL, to keep the prompt (and its cost) small.
    summary_data = {
        "domain": report["domain"],
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
    }

    return render_template("report.html", report=report, summary_data=summary_data)


@app.route("/summarize", methods=["POST"])
def summarize():
    data = request.get_json(silent=True) or {}
    summary, error = generate_summary(data)
    if error:
        return jsonify({"error": error}), 502
    return jsonify({"summary": summary})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

"""
pdf_export.py

Builds a downloadable PDF of a scan report. Uses fpdf2 -- a pure-Python
library with no native system dependencies. Deliberately not WeasyPrint or
wkhtmltopdf-based tools: those need Cairo/Pango/a real browser engine
installed at the OS level, which isn't guaranteed to exist in Render's
standard Python build environment. fpdf2 trades HTML/CSS convenience for
that reliability -- the layout below is built by hand rather than reusing
report.html's CSS.

Known limitation: fpdf2's core fonts are Latin-1 only (no bundled Unicode
font). Text is sanitized to Latin-1 before writing, so any genuinely
non-Latin characters in a scanned URL or schema value get replaced rather
than crashing the export -- a rare edge case, not a silent data problem for
the vast majority of sites.
"""

from datetime import datetime, timezone
from io import BytesIO

from fpdf import FPDF
from fpdf.fonts import FontFace

GOOD = (30, 122, 92)
WARN = (182, 134, 44)
BAD = (178, 58, 46)
INK = (21, 32, 28)
INK_MUTED = (91, 102, 96)
LINE = (220, 226, 220)

PAGE_MARGIN = 15


def _safe(text):
    """fpdf2's core fonts are Latin-1 only; sanitize rather than crash on a
    URL or schema value containing other characters."""
    if text is None:
        return ""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _tier(pct, none_ok=False):
    if pct is None:
        return None if none_ok else WARN
    if pct >= 70:
        return GOOD
    if pct >= 40:
        return WARN
    return BAD


class ReportPDF(FPDF):
    def header(self):
        pass  # no repeating header -- the report's own title page handles this

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(*INK_MUTED)
        self.cell(0, 8, _safe(f"Page {self.page_no()}"), align="C")


def _stat_box(pdf, x, y, w, h, label, value, color):
    pdf.set_xy(x, y)
    pdf.set_draw_color(*LINE)
    pdf.rect(x, y, w, h)
    pdf.set_xy(x + 3, y + 3)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*INK_MUTED)
    pdf.cell(w - 6, 5, _safe(label))
    pdf.set_xy(x + 3, y + 9)
    pdf.set_font("Courier", "B", size=12)
    pdf.set_text_color(*(color or INK))
    pdf.cell(w - 6, 8, _safe(value))


def _canonical_note(canonical):
    if not canonical:
        return ""
    status = canonical.get("status")
    if status == "multiple":
        return "Multiple canonical tags"
    if status == "cross_domain":
        return "Canonical -> different domain"
    if status == "missing":
        return "No canonical tag"
    if status == "other_page":
        health = canonical.get("target_health")
        if health and health.get("noindexed"):
            return "Canonical -> noindexed page"
        if health and health.get("error"):
            return "Canonical -> broken page"
    return ""


def generate_pdf(report):
    """Returns PDF bytes for the given report dict (the same shape scorer.py
    produces and report.html renders)."""
    pdf = ReportPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    pdf.add_page()

    # -- Title --
    pdf.set_font("Helvetica", "B", size=10)
    pdf.set_text_color(*GOOD)
    pdf.cell(0, 6, "GEO/AEO READINESS TEST", ln=True)
    pdf.set_font("Courier", "B", size=18)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 9, _safe(report.get("root_domain") or report.get("domain", "")), ln=True)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(*INK_MUTED)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(0, 6, _safe(f"GEO/AEO Readiness Test -- generated {generated}"), ln=True)
    pdf.ln(4)

    # -- Score --
    score = report.get("overall_score")
    score_color = _tier(score)
    if score is None:
        pdf.set_font("Courier", "B", size=28)
        pdf.set_text_color(*WARN)
        pdf.cell(0, 16, "No score", ln=True)
        pdf.set_font("Helvetica", size=10)
        pdf.set_text_color(*INK_MUTED)
        pdf.multi_cell(0, 5, _safe(
            "Couldn't scan any pages on this run -- see the AI crawler "
            "access notes below for why."
        ))
    else:
        pdf.set_font("Courier", "B", size=36)
        pdf.set_text_color(*score_color)
        pdf.cell(0, 18, _safe(f"{score}/100"), ln=True)
    pdf.ln(2)

    # -- Stat grid (2 rows x 4 cols) --
    stats = [
        ("Pages scanned", f"{report.get('total_pages_scanned', 0)}/{report.get('urls_found_total', 0)}", None),
        ("Schema coverage", f"{report.get('schema_coverage_pct', 0)}% ({report.get('pages_with_schema', 0)})", _tier(report.get("schema_coverage_pct"))),
        ("Schema quality", f"{report.get('schema_quality_pct', 0)}%", _tier(report.get("schema_quality_pct"))),
        ("Issues found", f"{report.get('total_issues', 0)} ({report.get('required_issues', 0)} req, {report.get('recommended_issues', 0)} rec" + (f", {report.get('unrecognized_issues', 0)} unrec" if report.get("unrecognized_issues", 0) > 0 else "") + ")", BAD if report.get("required_issues", 0) > 0 else (WARN if report.get("total_issues", 0) > 0 else GOOD)),
        ("AI crawler access", f"{report.get('crawler_access_pct', 0)}%", _tier(report.get("crawler_access_pct"))),
        ("llms.txt", f"{report.get('llms_txt_quality_pct', 0)}% quality" if report.get("llms_txt_present") else "Absent", _tier(report.get("llms_txt_quality_pct")) if report.get("llms_txt_present") else INK_MUTED),
        ("Noindexed pages", str(report.get("noindexed_count", 0)), BAD if report.get("noindexed_count", 0) > 0 else GOOD),
        ("Canonical issues", str(report.get("canonical_issue_count", 0)), BAD if report.get("canonical_issue_count", 0) > 0 else GOOD),
        ("Schema truthfulness", f"{report.get('truthfulness_issue_count', 0)} flagged", BAD if report.get("truthfulness_issue_count", 0) > 0 else GOOD),
        ("Malformed JSON-LD", str(report.get("malformed_jsonld_pages", 0)), BAD if report.get("malformed_jsonld_pages", 0) > 0 else GOOD),
        ("Heading structure", f"{report.get('heading_issue_pages', 0)} pages", BAD if report.get("heading_issue_pages", 0) > 0 else GOOD),
        ("Thin / JS-rendered", str(report.get("thin_content_pages", 0)), BAD if report.get("thin_content_pages", 0) > 0 else GOOD),
        ("Content freshness", f"{report.get('freshness_pct')}% (median {report.get('freshness_median_age_days')}d)" if report.get("freshness_median_age_days") is not None else "Not enough data", _tier(report.get("freshness_pct")) if report.get("freshness_median_age_days") is not None else INK_MUTED),
    ]
    # Orphan detection is only meaningful when the scan actually covered the
    # entire site -- a partial sample isn't a basis for this finding, so it
    # doesn't even appear here unless coverage was 100%, same as the live report.
    if report.get("sample_coverage_pct") == 100:
        stats.append(("Orphan pages", str(report.get("orphan_count", 0)), BAD if report.get("orphan_count", 0) > 0 else GOOD))
    col_w = (210 - 2 * PAGE_MARGIN) / 4
    row_h = 18
    start_y = pdf.get_y()
    for i, (label, value, color) in enumerate(stats):
        col = i % 4
        row = i // 4
        x = PAGE_MARGIN + col * col_w
        y = start_y + row * (row_h + 3)
        _stat_box(pdf, x, y, col_w - 3, row_h, label, value, color)
    pdf.set_y(start_y + ((len(stats) - 1) // 4 + 1) * (row_h + 3) + 4)

    # -- AI crawler breakdown --
    breakdown = report.get("ai_crawler_breakdown") or []
    if breakdown:
        pdf.set_font("Helvetica", "B", size=10)
        pdf.set_text_color(*INK)
        pdf.cell(0, 7, "AI Crawler Breakdown", ln=True)
        pdf.set_font("Helvetica", size=8)
        for c in breakdown:
            if c["allowed"] and c["explicit"]:
                label, color = "named & allowed", GOOD
            elif c["allowed"]:
                label, color = "inherited, allowed", GOOD
            elif not c["explicit"]:
                label, color = "inherited, blocked", WARN
            else:
                label, color = "named & blocked", BAD
            pdf.set_text_color(*INK)
            pdf.cell(45, 5, _safe(c["bot"]))
            pdf.set_text_color(*color)
            pdf.cell(0, 5, _safe(label), ln=True)
        pdf.ln(3)

    # -- Per-page table --
    pages = report.get("pages") or []
    if pages:
        pdf.set_font("Helvetica", "B", size=10)
        pdf.set_text_color(*INK)
        pdf.cell(0, 7, f"Pages scanned ({len(pages)})", ln=True)
        pdf.set_font("Helvetica", size=8)

        heading_style = FontFace(family="Helvetica", emphasis="BOLD", size_pt=8, color=255, fill_color=INK)
        with pdf.table(
            col_widths=(35, 25, 40),
            text_align=("LEFT", "LEFT", "LEFT"),
            line_height=4.5,
            headings_style=heading_style,
        ) as table:
            header_row = table.row()
            for h in ("URL", "Schema types", "Issues"):
                header_row.cell(h)
            for page in pages[:200]:  # hard ceiling so a pathological report can't produce an unbounded PDF
                row = table.row()
                row.cell(_safe(page["url"]))
                canon_note = _canonical_note(page.get("canonical"))
                orphan_note = "Orphan" if page.get("is_orphan") and report.get("sample_coverage_pct") == 100 else ""
                extra_notes = []
                if page.get("malformed_jsonld"):
                    extra_notes.append("Malformed JSON-LD")
                heading_info = page.get("heading_info") or {}
                if heading_info.get("h1_count") == 0:
                    extra_notes.append("Missing H1")
                elif heading_info.get("h1_count", 0) > 1:
                    extra_notes.append("Multiple H1 tags")
                if heading_info.get("skip_count", 0) > 0:
                    extra_notes.append("Heading level skip")
                if page.get("is_thin_content"):
                    extra_notes.append("Thin/JS-rendered content")
                if page.get("error"):
                    row.cell(_safe(page["error"]))
                    row.cell("")
                elif not page.get("schema_items"):
                    notes = [n for n in [
                        "Noindexed" if page.get("noindexed") else "",
                        orphan_note,
                        canon_note,
                    ] + extra_notes if n]
                    row.cell("None found")
                    row.cell(_safe(", ".join(notes)))
                else:
                    types = ", ".join(item["type"] for item in page["schema_items"])
                    issues = [n for n in [
                        "Noindexed" if page.get("noindexed") else "",
                        orphan_note,
                        canon_note,
                    ] + extra_notes if n]
                    for item in page["schema_items"]:
                        mismatch = item.get("content_mismatch")
                        if mismatch and mismatch["total"] and mismatch["mismatches"] / mismatch["total"] >= 0.5:
                            issues.append(f"{item['type']} content not found ({mismatch['mismatches']}/{mismatch['total']})")
                        issues += [f"Missing: {f}" for f in item.get("missing_required", [])]
                        issues += [f"Recommended: {f}" for f in item.get("missing_recommended", [])]
                    row.cell(_safe(types))
                    row.cell(_safe(", ".join(issues)))

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()

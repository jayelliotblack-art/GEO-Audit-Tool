"""
summarizer.py

Optional feature: turns the aggregate scan stats into a short, plain-English
summary using the Anthropic API. Triggered on-demand (a button), not run
automatically on every scan -- there's no reason to spend money generating
a summary nobody asked to see.

NOT free. Real but small: at Claude Haiku rates (the cheapest, fastest
model, and plenty for a structured summary like this), a call here costs
roughly $0.001 -- a tenth of a cent. Trivial at low volume, but a real line
item if this ever gets meaningful traffic.

Requires an ANTHROPIC_API_KEY environment variable set on the server (e.g.
in Render's dashboard, not in this code). The rest of the app works fine
without it -- this feature just returns a clear error instead of a summary.
"""

import os

import anthropic

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 300


def _build_prompt(data):
    blocked = data.get("blocked_ai_crawlers") or []
    breakdown = data.get("ai_crawler_breakdown") or []
    explicit_allowed = [c["bot"] for c in breakdown if c.get("allowed") and c.get("explicit")]
    explicit_blocked = [c["bot"] for c in breakdown if not c.get("allowed") and c.get("explicit")]

    return f"""You're summarizing a GEO/AEO (generative/AI engine optimization) structured-data audit for {data.get('domain')}, for someone who'll paste this into a client report or internal update. Write 3-5 plain-English sentences: lead with the headline takeaway, name the strongest finding, name the single most actionable gap. No headers, no bullet points, no filler -- just prose a busy person can use directly.

Data:
- Overall score: {data.get('overall_score')}/100
- Schema coverage: {data.get('schema_coverage_pct')}% of scanned pages have some structured data ({data.get('pages_with_schema')}/{data.get('total_pages_scanned')} pages)
- Schema quality: {data.get('schema_quality_pct')}% of detected schema entities are fully complete, no missing required or recommended fields ({data.get('scoreable_complete')}/{data.get('scoreable_total')})
- AI crawler access: {data.get('crawler_access_pct')}% of tracked AI crawlers can access the site; blocked: {', '.join(blocked) if blocked else 'none'}
- Explicitly allow-listed AI crawlers (a deliberate GEO decision): {', '.join(explicit_allowed) if explicit_allowed else 'none'}
- Explicitly blocked AI crawlers (a deliberate exclusion): {', '.join(explicit_blocked) if explicit_blocked else 'none'}
- llms.txt present: {data.get('llms_txt_present')}
- Noindexed pages found: {data.get('noindexed_count')}
- Canonical tag issues found: {data.get('canonical_issue_count')}
- Content freshness: median age {data.get('freshness_median_age_days')} days (None means not enough reliable lastmod data to judge)
"""


def generate_summary(data):
    """Returns (summary_text, error). error is None on success, otherwise a
    human-readable string safe to show in the UI."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "ANTHROPIC_API_KEY isn't set on the server -- this feature needs that configured first."

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": _build_prompt(data)}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return text.strip(), None
    except Exception as exc:
        return None, f"Couldn't generate a summary ({exc})"

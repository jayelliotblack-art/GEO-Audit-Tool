"""
geo_rules.py

This is the part of the tool that's actually yours to own, not something
sourced from a library. schema.org defines the full vocabulary, but it does
NOT define which fields Google requires for a rich result -- that's a
separate, narrower spec Google documents type-by-type and updates
periodically.

IMPORTANT: the required/recommended fields below are a reasonable starting
point based on general knowledge of Google's documented rich-result
guidelines, NOT a verified-today copy of their docs. Before trusting this for
real client-facing audits, cross-check each type against
https://developers.google.com/search/docs/appearance/structured-data and
adjust -- this is exactly the kind of curation that should run through your
judgment, not mine.
"""

import urllib.robotparser

# Required vs. recommended properties per schema type, for rich-result
# eligibility. Keys match the @type string as it appears in JSON-LD.
PRIORITY_TYPES = {
    "Article": {
        "required": ["headline", "image", "datePublished"],
        "recommended": ["dateModified", "author"],
    },
    "NewsArticle": {
        "required": ["headline", "image", "datePublished"],
        "recommended": ["dateModified", "author"],
    },
    "BlogPosting": {
        "required": ["headline", "image", "datePublished"],
        "recommended": ["dateModified", "author"],
    },
    "FAQPage": {
        "required": ["mainEntity"],
        "recommended": [],
    },
    "HowTo": {
        "required": ["name", "step"],
        "recommended": ["image", "totalTime", "estimatedCost"],
    },
    "Product": {
        "required": ["name"],
        "recommended": ["image", "description", "offers", "aggregateRating", "review"],
    },
    "LocalBusiness": {
        "required": ["name", "address"],
        "recommended": ["telephone", "openingHoursSpecification", "geo"],
    },
    "Organization": {
        "required": ["name", "url"],
        "recommended": ["logo", "sameAs"],
    },
    "Person": {
        "required": ["name"],
        "recommended": ["sameAs", "jobTitle"],
    },
    "BreadcrumbList": {
        "required": ["itemListElement"],
        "recommended": [],
    },
}

# Schema types that AI answer engines specifically pull from -- the part of
# this audit that's actually GEO-specific rather than generic SEO hygiene.
GEO_SIGNAL_TYPES = {"FAQPage", "HowTo", "SpeakableSpecification", "QAPage"}

# Known AI crawler user-agents to check for in robots.txt. New ones show up
# fairly often -- worth revisiting this list every few months.
AI_CRAWLER_USER_AGENTS = [
    "GPTBot",
    "ChatGPT-User",
    "ClaudeBot",
    "anthropic-ai",
    "PerplexityBot",
    "Google-Extended",
    "CCBot",
    "Amazonbot",
    "Bytespider",
    "Applebot-Extended",
    "meta-externalagent",
]


def check_required_fields(item, type_name):
    """Returns (missing_required, missing_recommended) for a single
    structured data item against our curated rules. Empty lists if the type
    isn't one we have a rule for."""
    rule = PRIORITY_TYPES.get(type_name)
    if not rule:
        return [], []
    missing_required = [f for f in rule["required"] if f not in item]
    missing_recommended = [f for f in rule["recommended"] if f not in item]
    return missing_required, missing_recommended


def check_ai_crawler_access(robots_txt, sample_urls):
    """For each known AI crawler, checks whether it would be blocked from
    the URLs we actually tried to scan -- not just a blanket 'Disallow: /'
    pattern. Real robots.txt files on larger sites usually block through
    many specific path rules rather than a single root-level disallow, and
    a bot with no explicit rule of its own falls through to whatever the
    '*' wildcard rule says. urllib.robotparser already implements that
    precedence correctly, so we lean on it directly rather than re-deriving
    it with string matching.

    A bot is reported as 'blocked' if it can't fetch ANY of the sampled
    URLs -- mirroring the same all-or-nothing situation our own crawler may
    have just hit."""
    if not robots_txt or not sample_urls:
        return []

    blocked = []
    for bot in AI_CRAWLER_USER_AGENTS:
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(robots_txt.splitlines())
        if not any(rp.can_fetch(bot, url) for url in sample_urls):
            blocked.append(bot)
    return blocked

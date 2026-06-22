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

from protego import Protego

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


def is_fully_complete(missing_required, missing_recommended, type_name):
    """Returns None if we have no PRIORITY_TYPES rule for this type (nothing
    to grade it against), otherwise True/False. Deliberately binary rather
    than partial-credit: a Product missing 3 of 5 recommended fields and one
    missing 1 of 5 are both 'incomplete' for this purpose -- the percentage
    that matters is how many of your detected entities are fully sorted,
    not an average that a few good ones can paper over."""
    rule = PRIORITY_TYPES.get(type_name)
    if not rule or (not rule["required"] and not rule["recommended"]):
        return None
    return not missing_required and not missing_recommended


# A missing recommended field counts for this fraction of a missing required
# field when computing weighted completeness. Required fields gate rich-
# result eligibility outright; recommended ones are enhancements -- treating
# them as equally damaging would overstate how broken a page actually is.
RECOMMENDED_WEIGHT = 0.25


def item_completeness_pct(missing_required, missing_recommended, type_name):
    """Returns None if there's no rule to grade against (same condition as
    is_fully_complete), otherwise a continuous 0-100 completeness score.
    Unlike is_fully_complete's all-or-nothing answer, this is what actually
    feeds the score: a Product missing one of five recommended fields and
    one missing all five aren't equally bad, and this reflects that."""
    rule = PRIORITY_TYPES.get(type_name)
    if not rule or (not rule["required"] and not rule["recommended"]):
        return None
    total_weight = len(rule["required"]) + len(rule["recommended"]) * RECOMMENDED_WEIGHT
    if total_weight == 0:
        return None
    missing_weight = len(missing_required) + len(missing_recommended) * RECOMMENDED_WEIGHT
    return max(0.0, (1 - missing_weight / total_weight) * 100)


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


def _explicitly_named_agents(robots_txt):
    """Returns the set of lowercased user-agent names explicitly written in
    the file, excluding '*'. Protego doesn't expose this distinction
    itself (it just resolves final permissions), so we scan the raw text
    directly -- this is the only part of the check that needs to."""
    named = set()
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == "user-agent":
            value = value.strip().lower()
            if value and value != "*":
                named.add(value)
    return named


def classify_ai_crawler_access(robots_txt, sample_urls):
    """For each known AI crawler, returns whether it can access the URLs we
    actually sampled, AND whether that's because the site deliberately named
    it in robots.txt or because it just inherits whatever the wildcard '*'
    rule happens to say. Those are very different findings: a bot explicitly
    allowed is a deliberate GEO decision; a bot that merely falls through a
    permissive wildcard got the same outcome by accident; a bot that falls
    through a *blocking* wildcard is usually just an oversight (nobody
    updated robots.txt for a newer bot category) rather than a deliberate
    block -- worth surfacing differently than 'they don't want AI crawlers
    here' when a site has explicitly named and blocked a bot.

    Returns a list of dicts: [{'bot': str, 'allowed': bool, 'explicit': bool}]
    A bot is 'allowed' if it can fetch at least one sampled URL -- mirroring
    the same all-or-nothing standard our own crawler is held to."""
    if not robots_txt or not sample_urls:
        # No robots.txt at all -- allowed by convention, and there's nothing
        # to have named anything in, so every result is 'inherited' by definition.
        return [{"bot": bot, "allowed": True, "explicit": False} for bot in AI_CRAWLER_USER_AGENTS]

    named = _explicitly_named_agents(robots_txt)
    rp = Protego.parse(robots_txt)
    results = []
    for bot in AI_CRAWLER_USER_AGENTS:
        allowed = any(rp.can_fetch(url, bot) for url in sample_urls)
        results.append({"bot": bot, "allowed": allowed, "explicit": bot.lower() in named})
    return results


def check_ai_crawler_access(robots_txt, sample_urls):
    """Thin wrapper kept for the simple blocked/not-blocked view used in the
    score and the headline callout; classify_ai_crawler_access has the full
    detail this is derived from."""
    return [c["bot"] for c in classify_ai_crawler_access(robots_txt, sample_urls) if not c["allowed"]]

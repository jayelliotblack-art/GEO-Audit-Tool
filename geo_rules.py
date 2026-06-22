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


def check_ai_crawler_access(robots_txt):
    """Parses robots.txt text and returns a list of AI crawler user-agents
    that are explicitly disallowed from the whole site (Disallow: /)."""
    if not robots_txt:
        return []

    blocked = []
    current_agents = []
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if value == "*":
                current_agents = ["*"]
            else:
                current_agents = [value]
        elif key == "disallow" and value in ("/", ""):
            for agent in current_agents:
                if agent == "*":
                    blocked.extend(AI_CRAWLER_USER_AGENTS)
                elif agent in AI_CRAWLER_USER_AGENTS:
                    blocked.append(agent)

    return sorted(set(blocked))

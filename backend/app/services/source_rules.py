# /backend/app/services/source_rules.py
"""Publisher registry and per-publisher extraction rules."""
from __future__ import annotations

from datetime import date

# Tavily rejects path patterns in include_domains for topic='news', and `site:`
# operators are ignored, so section scoping has to happen client-side. These
# paths are quote/profile shells that always come back with empty raw_content.
JUNK_URL_PATHS = (
    "/markets/companies/",
    "/quote/",
    "/quotes/",
    "/profile/company/",
    "/key-details",
    "/analytics",
    "/company/",
    # A stock-statistics landing page. It carries the ticker's PE ratio, VWAP
    # and dividend yield in a table, plus boilerplate explaining what each
    # ratio means — and its "opening paragraphs" are whatever the site last
    # published, so the subject classifier reads it as a page about the company
    # and every ratio becomes a fact. Measured on Wipro: 23 of 267 hard facts
    # came from `economictimes.indiatimes.com/<company>/stocks/companyid-*.cms`,
    # including "Investments in securities are subject to market risks".
    "/stocks/companyid-",
    "/stocks/companyid",
    # A publisher's own topic and company hubs, which are an index of its
    # coverage rather than a story. Measured: 32 of Wipro's 267 hard facts came
    # from `/topic/...` pages, carrying other companies' layoffs and a live
    # price ticker.
    "/topic/",
    "/topics/",
    "/tag/",
    "/tags/",
    # A "about the company" page is a directory entry: address, phone, sector.
    "/about/",
    # A live share-price blog. It reprints a company's regulatory filings under
    # a "Share Price Live Updates" heading and stamps every block with a
    # timestamp, so its "opening paragraphs" are quote ticks rather than
    # reporting. Measured on Wipro: five facts from one such page, including
    # "The data points are updated as on 03:40:13 PM IST, 12 Nov 2026".
    #
    # Deliberately "stock-page" and "share-price-page" rather than a bare
    # "/markets/stocks/": that directory also carries real articles, and
    # matching it dropped a Canada Goose press release in the same run.
    "/stock-liveblog/",
    "/stock-live/",
    "/price-live/",
    "/share-price-live/",
    "/stock-page/",
)

MIN_CLEAN_TEXT_LENGTH = 100

MIN_PARAGRAPH_LENGTH = 80

# Only the opening and closing paragraphs are treated as hard facts.
HARD_FACT_HEAD_PARAGRAPHS = 3

# Publisher indexes file articles under the publication year, so the keep
# prefixes must follow the calendar instead of a hard-coded year.
def recent_year_sections(years_back: int = 2) -> tuple[str, ...]:
    """Return the URL year segments that can still carry in-window articles."""
    current = date.today().year
    return tuple(f"/{year}/" for year in range(current, current - years_back - 1, -1))

# Middle paragraphs are only inspected for these leverage signals, so that
# commentator framing never reaches the fact base.
#
# Entries are canonical word forms: the matcher anchors on word boundaries and
# accepts an optional plural suffix, so "miss" no longer matches "mission" and
# "loss" still matches "losses". A trailing "*" marks a deliberate stem.
NEGATIVE_SIGNAL_KEYWORDS = (
    "layoff",
    "job cut",
    "cut jobs",
    "cut roles",
    "roles cut",
    "reduction in force",
    "restructur*",
    "cash flow",
    "cash-flow",
    "cash crunch",
    "liquidity",
    "loss",
    "net loss",
    "wider loss",
    "decline",
    "slump",
    "plunge",
    "fell",
    "fall",
    "drop",
    "miss",
    "shortfall",
    "below expectations",
    "cost cut",
    "cost-cutting",
    "margin pressure",
    "margin squeeze",
    "price cut",
    "price reduction",
    "discount",
    "resign*",
    "departure",
    "departed",
    "step down",
    "stepped down",
    "downsize*",
    "downturn",
    "warn*",
    "probe",
    "lawsuit",
    "litigation",
    "investigation",
    "sanction",
    "recall",
    "delay*",
    "halt*",
    "suspend*",
    "shutdown",
    "closure",
    "default",
    "insolven*",
    "bankrupt*",
    "distress",
    "downgrade",
    "weak",
    "weaker",
    "slowdown",
    "underperform*",
    "pressure",
    "burden",
    "overcapacit*",
)

# Cap the number of recovered negative sentences per source so one long
# article cannot flood the fact base.
NEGATIVE_SIGNAL_MAX_PER_SOURCE = 4

# Commentary is the weaker signal of the two, so it gets a smaller cap.
OPINION_MAX_PER_SOURCE = 3

# Tavily raw_content is page markdown, so the article body arrives wrapped in
# navigation trees, share rows, related-story blocks and legal boilerplate.
# Every publisher below was inspected on a live page to record exactly where
# the body opens and which non-body modules trail it.
#
# body_end:  lowercased substrings that close the body; the first hit wins.
# body_line: regexes for chrome lines that must never enter the body.
# paywalled: the page never delivers prose, only a subscription offer.
SITE_BODY_RULES: dict[str, dict] = {
    "reuters.com": {
        "body_end": ("our standards:", "the thomson reuters trust principles"),
        "body_line": (),
    },
    "bloomberg.com": {
        "body_end": (),
        "body_line": (
            r"\[skip to content\]",
            r"utm_source=bloomber",
            r"^\[(sign in|subscribe)\]",
            r"^\[\]\(",
            r"^### (for customers|support|company|communications|bloomberg)",
        ),
    },
    "wsj.com": {
        "body_end": ("## videos", "### most popular", "#### recommended videos"),
        "body_line": (r"copyright \u00a9202", r"^!\["),
    },
    "ft.com": {
        "body_end": ("subscribe to unlock this article", "## try unlimited access"),
        "body_line": (),
        "paywalled": True,
    },
    "nikkei.com": {
        "body_end": ("## read next", "## latest on", "## most popular"),
        "body_line": (r"^!\[", r"^\[\!\["),
    },
    "cnbc.com": {
        "body_end": (
            "see here for a full list of the stocks",
            "as a subscriber to the cnbc investing club",
            "the above investing club information is subject",
            "cnbc investing club with jim cramer",
        ),
        "body_line": (),
    },
    "finance.yahoo.com": {
        "body_end": (
            "## recommended stories",
            "## more stories",
            "terms and privacy policy",
            "this article originally published on",
        ),
        "body_line": (r"^\[\]\(", r"^!\[\]", r"^\[\!\["),
    },
    "businessinsider.com": {
        "body_end": ("## read next", "read comments start the conversation"),
        "body_line": (r"^\[\]\(", r"^\[(subscribe|the long play|watch bi)"),
    },
    "markets.businessinsider.com": {
        "body_end": (
            "\ncontact\nlisa",
            "executive vice president and chief financial officer",
        ),
        "body_line": (r"^\[\]\(",),
    },
    "autonews.gasgoo.com": {
        "body_end": (
            "### related documents",
            "### related articles",
            "### related posts",
        ),
        "body_line": (
            r"^\[\!\[",
            r"scan\s+for\s+mobile",
            r"^\[f share\]",
            r"^edited by",
        ),
    },
    "insideevs.com": {
        "body_end": ("share this story", "## comments", "## guides", "## latest"),
        "body_line": (
            r"^\*\s+\[(news|reviews|features|videos|podcasts|photos|newsletter|best evs)\]",
            r"^\[\!\[",
            r"^!\[\]",
            r"- the insideevs team",
        ),
    },
    "cnevpost.com": {
        "body_end": ("### about author", "### related posts"),
        "body_line": (
            r"^\|",
            r"add cn(e)?vpost as a preferred source",
            r"view more channels",
            r"^\[\+\s*add us on google",
        ),
    },
    "electrek.co": {
        "body_end": ("ftc: we use income earning", "## comments", "## guides"),
        "body_line": (
            r"advertisement - scroll",
            r"^\[\!\[",
            r"^!\[\]",
            r"^\[\]",
            r"^\*\*(add|subscribe)",
        ),
    },
}

# Editorial section rules per publisher. A URL is kept only when its path
# contains one of the keep prefixes and none of the drop prefixes.
#
# An empty keep tuple means "any path is acceptable".
SITE_SECTION_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "reuters.com": (
        ("/business/", "/markets/", "/legal/"),
        ("/world/", "/technology/"),
    ),
    "bloomberg.com": (
        (
            "/markets/",
            "/finance/",
            "/industries/",
            "/technology/",
            "/news/articles/",
        ),
        ("/quote/", "/profile/", "/opinion/", "/video/"),
    ),
    "wsj.com": (
        ("/business/", "/economy/", "/tech/", "/finance/", "/markets/"),
        ("/livecoverage/", "/podcasts/", "/video/", "/opinion/"),
    ),
    "cnbc.com": (
        ("/markets/", "/business/", "/technology/") + recent_year_sections(),
        ("/quotes/", "/video/", "/investingclub/", "/pro/"),
    ),
    "ft.com": (
        ("/companies/", "/technology/", "/markets/", "/content/"),
        ("/data/announce/", "/search/", "/opinion/"),
    ),
    "nikkei.com": (
        ("/business/", "/markets/", "/economy/", "/spotlight/"),
        ("/opinion/", "/politics/", "/video/"),
    ),
    "finance.yahoo.com": (
        ("/markets/", "/topic/latest-news/", "/daily-alpha/", "/news/"),
        ("/quote/", "/video/", "/finance/"),
    ),
    "businessinsider.com": (
        (
            "/business",
            "/economy",
            "/retail",
            "/law",
            "/tech",
            "/startups",
            "/innovation",
            "/news/",
        ),
        ("/recent-company-layoffs", "/lists/", "/video/", "/podcasts/"),
    ),
    "markets.businessinsider.com": (
        ("/stocks", "/news/stocks/"),
        ("/quote/", "/video/"),
    ),
    "autonews.gasgoo.com": (
        ("/articles/news/", "/articles/market-industry/", "/articles/ev/"),
        ("/articles/rankings/", "/articles/video/", "/articles/other/"),
    ),
    "insideevs.com": (
        ("/news/",),
        ("/reviews/", "/features/", "/video/"),
    ),
    "cnevpost.com": ((), ()),
    "electrek.co": ((), ()),
}

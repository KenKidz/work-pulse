"""ILIKE substring-search helpers shared by the Postgres adapters.

One implementation of the user-input escaping so search semantics cannot drift
between domains: call sites build the pattern with `contains_pattern()` and
pass `escape=LIKE_ESCAPE` to `ilike()`.
"""

LIKE_ESCAPE = "\\"


def contains_pattern(term: str) -> str:
    """Build an ILIKE pattern for substring search, escaping user wildcards."""
    escaped = (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"

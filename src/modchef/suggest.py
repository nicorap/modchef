"""Fuzzy 'did you mean' suggestions for not-found names."""
from difflib import get_close_matches


def closest(term, candidates, n=3, cutoff=0.6):
    """Return up to `n` candidates closest to `term`, case-insensitively.

    Matching ignores case, but the returned values keep each candidate's
    original casing (so module names like 'SAMtools/...' come back intact).
    """
    lower_to_orig = {}
    for c in candidates:
        lower_to_orig.setdefault(c.lower(), c)
    matches = get_close_matches(term.lower(), list(lower_to_orig), n=n,
                                cutoff=cutoff)
    return [lower_to_orig[m] for m in matches]

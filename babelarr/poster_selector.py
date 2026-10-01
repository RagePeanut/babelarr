"""Pure poster-selection logic.

Given the posters TMDB knows about for a title (each tagged with a language, or
"no language"/textless), the title's original language, and a ``POSTER_RULES``
rule set, decide which poster to apply.

A poster preference may be a concrete language code or the ``textless`` token,
which maps to TMDB's "no language / not specified" posters. NOTE: because TMDB
is community-maintained, a poster tagged "no language" is not *guaranteed* to be
free of title text — it is only categorized that way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .langcodes import normalize
from .rules import RuleSet, TOKEN_TEXTLESS


@dataclass(frozen=True)
class PosterView:
    """A candidate poster from TMDB.

    ``language_code`` is the ISO 639-1 code TMDB assigned, or ``None`` for the
    "no language / not specified" (textless) category.
    """

    key: str  # opaque identifier (e.g. TMDB file_path) used to apply later
    language_code: Optional[str]
    vote_average: float = 0.0


def select_poster(
    posters: Sequence[PosterView],
    original_language: Optional[str],
    rules: RuleSet,
) -> Optional[str]:
    """Return the ``key`` of the poster to apply, or ``None`` to leave untouched.

    The first matching rule (by original language) is authoritative. Its
    preferences are tried in order; for each, the highest-voted matching poster
    is chosen. ``textless`` matches posters with no language. If the matched
    rule yields nothing available, returns ``None`` (leave untouched) rather
    than falling through to ``default``.
    """
    prefs = rules.match(original_language)
    if prefs is None:
        return None

    for want in prefs:
        if want == TOKEN_TEXTLESS:
            candidates = [p for p in posters if normalize(p.language_code) is None]
        else:
            want_norm = normalize(want)
            if want_norm is None:
                continue
            candidates = [
                p for p in posters if normalize(p.language_code) == want_norm
            ]
        if not candidates:
            continue
        best = max(candidates, key=lambda p: p.vote_average)
        return best.key

    return None  # matched rule, nothing available -> untouched

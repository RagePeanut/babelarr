"""Pure title-selection logic.

Given the localized titles TMDB knows about for a title (each tagged with a
language), the title's original title + original language, and a ``TITLE_RULES``
rule set, decide which title string to apply to Plex.

A title preference may be a concrete language code or the ``original`` token,
which resolves to TMDB's ``original_title`` / ``original_name``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .langcodes import normalize
from .rules import RuleSet, TOKEN_ORIGINAL


@dataclass(frozen=True)
class TitleOptions:
    """All title strings Babelarr can choose from for one title.

    ``by_language`` maps a normalized (639-3) language code to the localized
    title in that language. ``original_title`` is TMDB's original title string.
    """

    by_language: Dict[str, str]
    original_title: Optional[str]
    original_language: Optional[str]


def select_title(options: TitleOptions, rules: RuleSet) -> Optional[str]:
    """Return the title string to apply, or ``None`` to leave untouched.

    The first rule matching the original language is authoritative; its
    preferences are tried in order. ``original`` resolves to the original title.
    If the matched rule yields no available title, returns ``None`` (leave
    untouched); it does not fall through to ``default``.
    """
    prefs = rules.match(options.original_language)
    if prefs is None:
        return None

    for want in prefs:
        if want == TOKEN_ORIGINAL:
            if options.original_title:
                return options.original_title
            continue
        want_norm = normalize(want.token)
        if want_norm is None:
            continue
        title = options.by_language.get(want_norm)
        if title:
            return title

    return None  # matched rule, nothing available -> untouched

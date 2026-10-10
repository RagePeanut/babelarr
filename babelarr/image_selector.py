"""Pure image-selection logic for logos and backdrops.

Logos (Plex "clearLogo") and backdrops (Plex "art") are chosen exactly like
posters: TMDB exposes, for a title, a set of candidate images each tagged with a
language (or "no language"/textless), and a rule set keyed by the title's
PRODUCTION language decides which one to apply. This module factors out that
shared selection so :mod:`babelarr.poster_selector` stays untouched while logos
and backdrops reuse the same, well-tested logic.

Two differences from posters are expressed via parameters rather than new
modules:

* **Logos** additionally accept the ``off`` token, meaning "apply NO logo"
  (clear it) so Plex falls back to the text display title. ``off`` is the one
  token that resolves to a decision even when no image is available, so it is
  reported through a dedicated sentinel (:data:`SELECT_OFF`) rather than an
  image key.
* **Backdrops** are, in practice, almost always TMDB "no language" images, so
  ``textless`` is the natural default preference; no behavioral change is needed
  for that -- it already works via the shared ``textless`` handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .langcodes import normalize
from .rules import RuleSet, TOKEN_OFF, TOKEN_ORIGINAL, TOKEN_TEXTLESS

# Sentinel returned when a matched rule selects the ``off`` token: the caller
# must CLEAR the image (so Plex falls back to its default/text), as opposed to
# ``None`` which means "leave the field untouched".
SELECT_OFF = "\x00off"


@dataclass(frozen=True)
class ImageView:
    """A candidate logo/backdrop from TMDB.

    ``language_code`` is the ISO 639-1 code TMDB assigned, or ``None`` for the
    "no language / not specified" (textless) category. ``key`` is an opaque
    identifier (the absolute image URL) used to apply the image later.
    """

    key: str
    language_code: Optional[str]
    vote_average: float = 0.0


def select_image(
    images: Sequence[ImageView],
    original_language: Optional[str],
    rules: RuleSet,
) -> Optional[str]:
    """Return the ``key`` of the image to apply, :data:`SELECT_OFF`, or ``None``.

    The first rule matching ``original_language`` is authoritative; its
    preferences are tried in order and the FIRST available one wins:

    * a concrete language / ``original`` -> the highest-voted matching image's
      key;
    * ``textless`` -> the highest-voted "no language" image's key;
    * ``off`` -> :data:`SELECT_OFF` (clear the image). ``off`` is always
      "available", so any preferences listed after it are unreachable -- mirror
      of how subtitle ``off`` behaves.

    Returns ``None`` when no rule matches, or the matched rule's preferences are
    all unavailable (leave the field untouched -- never falls through to
    ``default``).
    """
    prefs = rules.match(original_language)
    if prefs is None:
        return None

    for want in prefs:
        if want == TOKEN_OFF:
            return SELECT_OFF
        if want == TOKEN_TEXTLESS:
            candidates = [img for img in images if normalize(img.language_code) is None]
        else:
            want_lang = original_language if want == TOKEN_ORIGINAL else want.token
            want_norm = normalize(want_lang)
            if want_norm is None:
                continue
            candidates = [
                img for img in images if normalize(img.language_code) == want_norm
            ]
        if not candidates:
            continue
        best = max(candidates, key=lambda img: img.vote_average)
        return best.key

    return None  # matched rule, nothing available -> untouched

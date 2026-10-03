"""ISO 639 language-code normalization.

Different sources use different ISO 639 variants for the same language:

* TMDB `original_language` -> ISO 639-1 (2-letter), e.g. ``en``, ``fr``, ``ja``.
* Plex track ``languageCode`` -> usually ISO 639-2 (3-letter), and for ~20
  languages there are two 639-2 variants: bibliographic (``fre``, ``ger``) and
  terminographic (``fra``, ``deu``).

We normalize everything to a single canonical ISO 639-3 code so comparisons
become simple string equality. ``pycountry`` ships the full ISO 639 database
and resolves all these aliases; a small override table on top covers the rare
non-standard codes Plex occasionally emits.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

try:  # pycountry provides the full ISO 639 database when available.
    import pycountry
except Exception:  # pragma: no cover - exercised only without pycountry
    pycountry = None

# Non-standard / legacy codes that pycountry does not resolve, mapped to the
# canonical ISO 639-3 alpha_3 value.
_OVERRIDES = {
    "in": "ind",  # legacy Indonesian
    "iw": "heb",  # legacy Hebrew
    "ji": "yid",  # legacy Yiddish
    "cz": "ces",  # occasional non-standard Czech
    "gr": "ell",  # occasional non-standard Greek
}

# Built-in fallback for common languages, used when pycountry is unavailable.
# Maps any of {alpha_2, 639-2/B, 639-2/T} -> canonical alpha_3 (639-2/T).
_FALLBACK = {
    "en": "eng", "eng": "eng",
    "fr": "fra", "fre": "fra", "fra": "fra",
    "de": "deu", "ger": "deu", "deu": "deu",
    "es": "spa", "spa": "spa",
    "it": "ita", "ita": "ita",
    "pt": "por", "por": "por",
    "ja": "jpn", "jpn": "jpn",
    "ko": "kor", "kor": "kor",
    "zh": "zho", "chi": "zho", "zho": "zho",
    "ru": "rus", "rus": "rus",
    "nl": "nld", "dut": "nld", "nld": "nld",
    "sv": "swe", "swe": "swe",
    "no": "nor", "nor": "nor",
    "da": "dan", "dan": "dan",
    "fi": "fin", "fin": "fin",
    "pl": "pol", "pol": "pol",
    "cs": "ces", "cze": "ces", "ces": "ces",
    "el": "ell", "gre": "ell", "ell": "ell",
    "hi": "hin", "hin": "hin",
    "ar": "ara", "ara": "ara",
    "he": "heb", "heb": "heb",
    "tr": "tur", "tur": "tur",
    "th": "tha", "tha": "tha",
    "id": "ind", "ind": "ind",
    "hu": "hun", "hun": "hun",
    "ro": "ron", "rum": "ron", "ron": "ron",
    "uk": "ukr", "ukr": "ukr",
    "vi": "vie", "vie": "vie",
}


@lru_cache(maxsize=512)
def normalize(code: Optional[str]) -> Optional[str]:
    """Map any ISO 639-1 / 639-2B / 639-2T code to canonical 639-3 alpha_3.

    Returns ``None`` for empty, unknown, or the special values Plex uses for
    "undetermined" language tracks.
    """
    if not code:
        return None
    code = code.strip().lower()
    if not code or code in ("und", "mis", "zxx", "mul"):
        return None
    if code in _OVERRIDES:
        return _OVERRIDES[code]

    if pycountry is not None:
        lang = (
            pycountry.languages.get(alpha_2=code)
            or pycountry.languages.get(alpha_3=code)
            or pycountry.languages.get(bibliographic=code)
        )
        if lang is not None:
            # Some entries only expose alpha_3; all have it via these APIs.
            resolved = getattr(lang, "alpha_3", None)
            if resolved:
                return resolved

    # Fallback table (also the path when pycountry is not installed).
    return _FALLBACK.get(code)


def same_language(a: Optional[str], b: Optional[str]) -> bool:
    """True if two codes refer to the same language after normalization."""
    na, nb = normalize(a), normalize(b)
    return na is not None and na == nb


# Canonical "no spoken language" sentinel (silent films, music-only).
# TMDB uses "xx" in spoken_languages for this; we also accept "silent" (friendly
# alias) and the ISO "no linguistic content" code "zxx". It is intentionally
# distinct from ``None`` (which means "unknown / undeterminable").
NO_LANGUAGE = "xx"
_NO_LANGUAGE_CODES = {"xx", "zxx", "silent"}


def is_no_language(code: Optional[str]) -> bool:
    """True if ``code`` is a 'no spoken language' marker (xx / zxx / silent)."""
    if not code:
        return False
    return code.strip().lower() in _NO_LANGUAGE_CODES

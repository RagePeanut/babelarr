"""Script-class resolution for a title's original language.

Babelarr lets rules key on the *writing system* of a title's original
language instead of (or in addition to) the language itself. This is handy for
coarse decisions like "any CJK-script title -> English title" without listing
every language individually.

Script classes form a small hierarchy. ``cjk`` is an umbrella covering three
leaf classes:

    cjk
    ├── han      (Chinese; also the Han component of Japanese/Korean)
    ├── kana     (Japanese)
    └── hangul   (Korean)

All other classes are flat (no children):

    latin, cyrillic, greek, arabic, hebrew, devanagari, thai, ...

Each original language maps to exactly one *leaf* class (``jpn`` -> ``kana``),
so leaf classes are mutually disjoint. A parent class (``cjk``) covers all of
its leaves. This "covers" relation is what the config validator uses to detect
unreachable rules (e.g. a ``cjk`` rule placed above a ``kana`` rule).

The language->script table is intentionally pragmatic: it covers the languages
that realistically appear as a film/show *original language* on TMDB. Unknown
languages simply have no script class (they only match concrete-language rules
or ``default``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Optional, Set

from .langcodes import normalize

# Umbrella class -> its leaf children. Leaves are disjoint; a parent covers all
# of its leaves (and itself).
_GROUPS: Dict[str, Set[str]] = {
    "cjk": {"han", "kana", "hangul"},
}

# Reverse map: leaf -> parent umbrella.
_PARENT_OF: Dict[str, str] = {
    leaf: parent for parent, leaves in _GROUPS.items() for leaf in leaves
}

# All valid script-class keys a rule may use (umbrellas + leaves + flat ones).
_FLAT_CLASSES: Set[str] = {
    "latin",
    "cyrillic",
    "greek",
    "arabic",
    "hebrew",
    "devanagari",
    "thai",
    "armenian",
    "georgian",
}

SCRIPT_CLASSES: Set[str] = (
    set(_GROUPS.keys()) | set(_PARENT_OF.keys()) | _FLAT_CLASSES
)

# Canonical ISO 639-3 language code -> leaf script class.
# Only languages that plausibly occur as an original language are listed.
_LANG_TO_LEAF: Dict[str, str] = {
    # --- CJK leaves ---
    "jpn": "kana",
    "zho": "han",
    "cmn": "han",
    "yue": "han",
    "kor": "hangul",
    # --- Cyrillic ---
    "rus": "cyrillic",
    "ukr": "cyrillic",
    "bul": "cyrillic",
    "srp": "cyrillic",
    "mkd": "cyrillic",
    "bel": "cyrillic",
    "kaz": "cyrillic",
    "mon": "cyrillic",
    # --- Greek ---
    "ell": "greek",
    # --- Arabic script ---
    "ara": "arabic",
    "fas": "arabic",  # Persian/Farsi
    "per": "arabic",
    "urd": "arabic",
    "pus": "arabic",
    # --- Hebrew ---
    "heb": "hebrew",
    "yid": "hebrew",
    # --- Devanagari ---
    "hin": "devanagari",
    "mar": "devanagari",
    "nep": "devanagari",
    "san": "devanagari",
    # --- Thai ---
    "tha": "thai",
    # --- Armenian / Georgian ---
    "hye": "armenian",
    "kat": "georgian",
}

# A broad set of Latin-script languages. Not exhaustive, but covers the common
# European and romanized originals. Anything here resolves to ``latin``.
_LATIN_LANGS: Set[str] = {
    "eng", "fra", "deu", "spa", "ita", "por", "nld", "swe", "nor", "dan",
    "fin", "pol", "ces", "slk", "slv", "hrv", "hun", "ron", "cat", "eus",
    "glg", "isl", "gle", "cym", "tur", "vie", "ind", "msa", "tgl", "swa",
    "afr", "sqi", "est", "lav", "lit", "mlt", "lat",
}


@lru_cache(maxsize=512)
def script_class_for(language_code: Optional[str]) -> Optional[str]:
    """Return the *leaf* script class for an original language code.

    Returns ``None`` when the language is unknown or has no mapped script.
    Umbrella classes (e.g. ``cjk``) are never returned here — a language always
    resolves to a concrete leaf (``kana``), and ``covers`` handles the parent
    relationship.
    """
    norm = normalize(language_code)
    if norm is None:
        return None
    if norm in _LANG_TO_LEAF:
        return _LANG_TO_LEAF[norm]
    if norm in _LATIN_LANGS:
        return "latin"
    return None


def is_script_class(key: str) -> bool:
    """True if ``key`` is a recognized script-class rule key."""
    return key.strip().lower() in SCRIPT_CLASSES


def _leaves_of(script_class: str) -> Set[str]:
    """Leaf classes represented by a script-class key (itself if already a leaf)."""
    sc = script_class.strip().lower()
    if sc in _GROUPS:
        return set(_GROUPS[sc])
    return {sc}


def language_matches_class(language_code: Optional[str], script_class: str) -> bool:
    """True if a title's original language falls under ``script_class``."""
    leaf = script_class_for(language_code)
    if leaf is None:
        return False
    return leaf in _leaves_of(script_class)


def class_covers_class(broader: str, narrower: str) -> bool:
    """True if script class ``broader`` fully covers ``narrower``.

    Used by the config validator. ``cjk`` covers ``kana``/``han``/``hangul``
    and itself; a leaf covers only itself; sibling leaves never cover each
    other.
    """
    return _leaves_of(narrower).issubset(_leaves_of(broader))


def class_covers_language(script_class: str, language_code: Optional[str]) -> bool:
    """True if ``script_class`` covers a concrete original language."""
    return language_matches_class(language_code, script_class)

"""Tests for content-language resolution (spoken vs production, no-language)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.langcodes import NO_LANGUAGE  # noqa: E402
from babelarr.langresolve import resolve_content_language  # noqa: E402


def R(prod, spoken, override=None):
    return resolve_content_language(prod, spoken, override)


# 1. Manual override wins outright.
def test_override_wins():
    assert R("en", ["ja", "en"], override="ko") == "kor"


def test_override_accepts_any_iso_form():
    assert R("en", ["ja"], override="fr") == "fra"
    assert R("en", ["ja"], override="fre") == "fra"
    assert R("en", ["ja"], override="fra") == "fra"


def test_override_can_force_no_language():
    assert R("de", ["de"], override="silent") == NO_LANGUAGE
    assert R("de", ["de"], override="xx") == NO_LANGUAGE


def test_override_ignored_when_blank_or_junk():
    assert R("en", ["ja"], override="") == "jpn"
    assert R("en", ["ja"], override="zzz") == "jpn"


# 2. Only "no language" spoken -> NO_LANGUAGE (Metropolis / silent).
def test_only_no_language_spoken_is_silent():
    assert R("de", ["xx"]) == NO_LANGUAGE


def test_multiple_no_language_markers_is_silent():
    assert R("de", ["xx", "zxx"]) == NO_LANGUAGE


# 3. Exactly one real spoken language.
def test_single_spoken_language_uzumaki():
    # Production 'en' but spoken only 'ja' -> Japanese.
    assert R("en", ["ja"]) == "jpn"


def test_single_spoken_matches_production():
    assert R("en", ["en"]) == "eng"


def test_single_real_after_dropping_no_language():
    # Silent marker alongside one real language -> the real one.
    assert R("de", ["xx", "ja"]) == "jpn"


# 4. Multiple real, production among them -> production (Nine to Five).
def test_nine_to_five_prefers_production_even_if_not_first():
    # spoken=[French, English], production=English -> English (not French!).
    assert R("en", ["fr", "en"]) == "eng"


def test_multiple_production_among_when_first():
    assert R("en", ["en", "fr"]) == "eng"


# 5. Multiple real, production NOT among them -> first real spoken.
def test_multiple_production_absent_uses_first_spoken():
    assert R("en", ["ja", "ko"]) == "jpn"


def test_multiple_production_none_uses_first_spoken():
    assert R(None, ["ja", "ko"]) == "jpn"


# 6. No spoken at all -> production.
def test_no_spoken_falls_back_to_production():
    assert R("de", []) == "deu"


def test_no_spoken_no_production_is_none():
    assert R(None, []) is None


# normalization / junk handling
def test_spoken_entries_normalized_and_deduped():
    assert R("en", [None, "", "ja", "ja"]) == "jpn"

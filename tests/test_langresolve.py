"""Tests for content-language resolution (spoken-language vs production)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.langresolve import resolve_content_language  # noqa: E402


def R(prod, spoken, audio=(), override=None):
    return resolve_content_language(prod, spoken, audio, override)


# 1. Manual override wins outright.
def test_override_wins():
    assert R("en", ["ja", "en"], audio=["en"], override="ko") == "kor"


def test_override_accepts_any_iso_form():
    # 639-1, 639-2/B, 639-2/T all normalize to the same canonical code.
    assert R("en", ["ja"], override="fr") == "fra"
    assert R("en", ["ja"], override="fre") == "fra"
    assert R("en", ["ja"], override="fra") == "fra"


def test_override_ignored_when_blank():
    # Empty/invalid override falls through to normal resolution.
    assert R("en", ["ja"], override="") == "jpn"
    assert R("en", ["ja"], override="zzz") == "jpn"


# 2. Exactly one spoken language.
def test_single_spoken_language_uzumaki():
    # Production 'en' but spoken only 'ja' -> content is Japanese.
    assert R("en", ["ja"]) == "jpn"


def test_single_spoken_matches_production():
    assert R("en", ["en"]) == "eng"


# 3. Multiple spoken, first == production.
def test_multiple_first_equals_production():
    # First spoken (en) equals production -> use it, don't consult audio.
    assert R("en", ["en", "ja"], audio=["ja"]) == "eng"


# 4. Multiple spoken, first != production -> walk list vs available audio.
def test_multiple_walk_available_audio():
    # Production fr, spoken [ja, ko], audio has only ko -> ko.
    assert R("fr", ["ja", "ko"], audio=["ko"]) == "kor"


def test_multiple_walk_prefers_order():
    # Both ja and ko available -> first in spoken order (ja) wins.
    assert R("fr", ["ja", "ko"], audio=["ko", "ja"]) == "jpn"


# 5. Multiple, none available as audio -> first spoken.
def test_multiple_none_available_first_spoken():
    assert R("fr", ["ja", "ko"], audio=["en"]) == "jpn"


def test_multiple_no_audio_info_first_spoken():
    assert R("fr", ["ja", "ko"], audio=[]) == "jpn"


# 6. No spoken languages -> production language.
def test_no_spoken_falls_back_to_production():
    assert R("de", [], audio=["de"]) == "deu"


def test_no_spoken_no_production_is_none():
    assert R(None, []) is None


# normalization / junk handling
def test_spoken_entries_normalized_and_filtered():
    # Blank/None spoken entries are ignored.
    assert R("en", [None, "", "ja"]) == "jpn"


def test_single_valid_spoken_after_filtering():
    # After filtering junk, only one real spoken language remains.
    assert R("en", ["", "fr", None]) == "fra"

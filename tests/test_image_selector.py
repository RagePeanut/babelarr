"""Tests for the pure logo/backdrop selector (babelarr.image_selector).

Mirrors the poster selector's semantics (first matching rule authoritative,
first available preference wins, textless = TMDB "no language"), plus the
logo-only ``off`` token which resolves to the SELECT_OFF sentinel ("clear the
image so Plex falls back to the text title").
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.config import BACKDROP_TOKENS, LOGO_TOKENS  # noqa: E402
from babelarr.image_selector import ImageView, SELECT_OFF, select_image  # noqa: E402
from babelarr.rules import parse_inline  # noqa: E402


def _logo_rules(spec):
    return parse_inline(spec, LOGO_TOKENS)


def _backdrop_rules(spec):
    return parse_inline(spec, BACKDROP_TOKENS)


EN = ImageView(key="http://img/en.png", language_code="en", vote_average=5.0)
EN_HI = ImageView(key="http://img/en_hi.png", language_code="en", vote_average=9.0)
FR = ImageView(key="http://img/fr.png", language_code="fr", vote_average=7.0)
NONE1 = ImageView(key="http://img/none.png", language_code=None, vote_average=3.0)
NONE2 = ImageView(key="http://img/none2.png", language_code="", vote_average=8.0)


def test_picks_concrete_language():
    key = select_image([EN, FR], "fr", _logo_rules("default:eng,original"))
    assert key == EN.key


def test_original_resolves_to_production_language():
    # original -> production language (fr here); FR logo present.
    key = select_image([EN, FR], "fr", _logo_rules("default:original"))
    assert key == FR.key


def test_highest_voted_within_a_language_wins():
    key = select_image([EN, EN_HI, FR], "fr", _logo_rules("default:eng"))
    assert key == EN_HI.key  # 9.0 beats 5.0


def test_textless_matches_no_language_images():
    key = select_image([EN, NONE1, NONE2], "fr", _backdrop_rules("default:textless"))
    assert key == NONE2.key  # highest-voted textless (8.0 > 3.0)


def test_first_available_preference_wins():
    # Wants fr first; no fr logo -> falls to eng.
    key = select_image([EN, NONE1], "de", _logo_rules("default:fre,eng"))
    assert key == EN.key


def test_no_matching_rule_returns_none():
    # Rule keyed on jpn; production is fr -> no match -> untouched.
    key = select_image([EN, FR], "fr", _logo_rules("jpn:eng"))
    assert key is None


def test_matched_rule_nothing_available_returns_none():
    # Matches default, but neither de nor a de-equivalent logo exists -> None
    # (does NOT fall through to anything).
    key = select_image([EN, FR], "it", _logo_rules("default:deu"))
    assert key is None


def test_empty_rules_returns_none():
    from babelarr.rules import RuleSet
    assert select_image([EN, FR], "fr", RuleSet()) is None


# --- logo-only `off` token --------------------------------------------------

def test_off_returns_select_off_sentinel():
    key = select_image([EN, FR], "fr", _logo_rules("default:off"))
    assert key == SELECT_OFF


def test_off_wins_over_later_preferences():
    # off is always "available", so eng after it is unreachable at runtime.
    key = select_image([EN], "fr", _logo_rules("default:off,eng"))
    assert key == SELECT_OFF


def test_off_still_only_fires_on_matching_key():
    # off is under jpn; production fr doesn't match -> untouched (None).
    key = select_image([EN], "fr", _logo_rules("jpn:off;eng:eng"))
    assert key is None


def test_the_purge_scenario_prefers_english_logo_over_french():
    # The reported case: a French "American Nightmare" logo and an English
    # "The Purge" logo both exist; production language is en. default:[eng,
    # original] must pick the English logo.
    en_logo = ImageView(key="http://img/the-purge-en.png", language_code="en",
                         vote_average=6.0)
    fr_logo = ImageView(key="http://img/american-nightmare-fr.png",
                        language_code="fr", vote_average=10.0)
    key = select_image([fr_logo, en_logo], "en", _logo_rules("default:eng,original"))
    assert key == en_logo.key  # English chosen despite the FR logo's higher vote

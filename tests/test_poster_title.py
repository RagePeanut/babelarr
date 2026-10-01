"""Tests for the pure poster and title selectors."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.config import POSTER_TOKENS, TITLE_TOKENS  # noqa: E402
from babelarr.poster_selector import PosterView, select_poster  # noqa: E402
from babelarr.rules import parse_inline  # noqa: E402
from babelarr.title_selector import TitleOptions, select_title  # noqa: E402


def _posters(value):
    return parse_inline(value, POSTER_TOKENS)


def _titles(value):
    return parse_inline(value, TITLE_TOKENS)


# --- posters ---------------------------------------------------------------

def _p(key, lang, vote=0.0):
    return PosterView(key=key, language_code=lang, vote_average=vote)


def test_poster_picks_french_for_french_film():
    rules = _posters("fre:fra;default:eng")
    posters = [_p("fr.jpg", "fr"), _p("en.jpg", "en")]
    assert select_poster(posters, "fr", rules) == "fr.jpg"


def test_poster_default_english():
    rules = _posters("fre:fra;default:eng")
    posters = [_p("en.jpg", "en"), _p("de.jpg", "de")]
    assert select_poster(posters, "de", rules) == "en.jpg"


def test_poster_textless_token():
    rules = _posters("jpn:jpn,textless;default:eng")
    posters = [_p("none.jpg", None), _p("en.jpg", "en")]
    # No Japanese poster, so falls to textless (no-language).
    assert select_poster(posters, "ja", rules) == "none.jpg"


def test_poster_original_token():
    # 'original' resolves to the title's original language.
    rules = _posters("default:original,en")
    posters = [_p("ja.jpg", "ja"), _p("en.jpg", "en")]
    assert select_poster(posters, "ja", rules) == "ja.jpg"  # original = Japanese


def test_poster_original_falls_through_when_absent():
    rules = _posters("default:original,en")
    posters = [_p("en.jpg", "en")]  # no original-language (ja) poster
    assert select_poster(posters, "ja", rules) == "en.jpg"


def test_poster_highest_vote_wins_within_language():
    rules = _posters("default:eng")
    posters = [_p("en-low.jpg", "en", vote=1.0), _p("en-high.jpg", "en", vote=9.0)]
    assert select_poster(posters, "de", rules) == "en-high.jpg"


def test_poster_matched_but_missing_leaves_untouched():
    rules = _posters("fre:fra")  # no default
    posters = [_p("en.jpg", "en")]
    assert select_poster(posters, "fr", rules) is None


def test_poster_no_match_untouched():
    rules = _posters("fre:fra")
    posters = [_p("en.jpg", "en")]
    assert select_poster(posters, "de", rules) is None


# --- titles ----------------------------------------------------------------

def _opts(by_lang, original_title, original_language):
    return TitleOptions(
        by_language=by_lang,
        original_title=original_title,
        original_language=original_language,
    )


def test_title_french_for_french_film():
    rules = _titles("fre:fra;default:eng")
    opts = _opts({"fra": "Le Film", "eng": "The Film"}, "Le Film", "fr")
    assert select_title(opts, rules) == "Le Film"


def test_title_original_token():
    rules = _titles("jpn:original;default:eng")
    opts = _opts({"eng": "Spirited Away"}, "千と千尋の神隠し", "ja")
    assert select_title(opts, rules) == "千と千尋の神隠し"


def test_title_script_class_cjk_to_english():
    rules = _titles("cjk:eng;default:original")
    opts = _opts({"eng": "Oldboy"}, "올드보이", "ko")
    assert select_title(opts, rules) == "Oldboy"


def test_title_default_original():
    rules = _titles("cjk:eng;default:original")
    opts = _opts({"eng": "The Film"}, "Original", "fr")
    assert select_title(opts, rules) == "Original"


def test_title_matched_but_missing_untouched():
    rules = _titles("fre:fra")  # no default
    opts = _opts({"eng": "The Film"}, "Orig", "fr")
    assert select_title(opts, rules) is None

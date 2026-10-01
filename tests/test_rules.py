"""Tests for the unified rule engine: parsing, matching, and order validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from babelarr.config import POSTER_TOKENS, SUBTITLE_TOKENS, TITLE_TOKENS  # noqa: E402
from babelarr.rules import (  # noqa: E402
    RuleError,
    parse_inline,
    parse_yaml,
)
from babelarr.langscript import (  # noqa: E402
    class_covers_class,
    language_matches_class,
    script_class_for,
)


# --- script classes --------------------------------------------------------

def test_script_class_mapping():
    assert script_class_for("ja") == "kana"
    assert script_class_for("zh") == "han"
    assert script_class_for("ko") == "hangul"
    assert script_class_for("ru") == "cyrillic"
    assert script_class_for("el") == "greek"
    assert script_class_for("en") == "latin"
    assert script_class_for("xx-unknown") is None


def test_cjk_umbrella_covers_leaves():
    assert class_covers_class("cjk", "kana")
    assert class_covers_class("cjk", "han")
    assert class_covers_class("cjk", "hangul")
    assert class_covers_class("cjk", "cjk")
    assert not class_covers_class("kana", "cjk")
    assert not class_covers_class("kana", "han")  # siblings disjoint


def test_language_matches_class():
    assert language_matches_class("ja", "kana")
    assert language_matches_class("ja", "cjk")
    assert not language_matches_class("ja", "han")
    assert language_matches_class("ru", "cyrillic")


# --- parsing + matching ----------------------------------------------------

def test_inline_match_by_language():
    rs = parse_inline("fre:fra;default:eng", POSTER_TOKENS)
    assert rs.match("fr") == ["fra"]
    assert rs.match("de") == ["eng"]  # default


def test_match_by_script_class():
    rs = parse_inline("cjk:eng;default:fra", TITLE_TOKENS)
    assert rs.match("ja") == ["eng"]
    assert rs.match("ko") == ["eng"]
    assert rs.match("fr") == ["fra"]


def test_leaf_class_then_broader_order():
    # kana first (specific), then cjk (broader) -> both reachable.
    rs = parse_inline("kana:eng;cjk:fra;default:original", TITLE_TOKENS)
    assert rs.match("ja") == ["eng"]       # kana
    assert rs.match("zh") == ["fra"]       # cjk (han)
    assert rs.match("en") == ["original"]  # default


def test_no_match_returns_none():
    rs = parse_inline("fre:fra", POSTER_TOKENS)
    assert rs.match("ja") is None


# --- token validation ------------------------------------------------------

def test_poster_textless_token_ok():
    rs = parse_inline("jpn:jpn,textless;default:eng,textless", POSTER_TOKENS)
    assert rs.match("ja") == ["jpn", "textless"]


def test_off_token_rejected_for_posters():
    with pytest.raises(RuleError):
        parse_inline("eng:off", POSTER_TOKENS)


def test_textless_token_rejected_for_subtitles():
    with pytest.raises(RuleError):
        parse_inline("eng:textless", SUBTITLE_TOKENS)


def test_original_token_rejected_for_posters():
    with pytest.raises(RuleError):
        parse_inline("eng:original", POSTER_TOKENS)


# --- order / consistency validation ----------------------------------------

def test_duplicate_key_errors():
    with pytest.raises(RuleError, match="Duplicate"):
        parse_inline("fre:fra;fre:eng", POSTER_TOKENS)


def test_duplicate_key_different_iso_form_errors():
    with pytest.raises(RuleError, match="Duplicate"):
        parse_inline("fr:fra;fre:eng", POSTER_TOKENS)


def test_default_must_be_last():
    with pytest.raises(RuleError, match="must be the last"):
        parse_inline("default:eng;fre:fra", POSTER_TOKENS)


def test_umbrella_before_leaf_is_unreachable():
    with pytest.raises(RuleError, match="Unreachable"):
        parse_inline("cjk:eng;kana:fra", TITLE_TOKENS)


def test_leaf_before_language_is_unreachable():
    with pytest.raises(RuleError, match="Unreachable"):
        parse_inline("kana:eng;jpn:fra", TITLE_TOKENS)


def test_sibling_leaves_are_fine():
    rs = parse_inline("kana:eng;han:fra;default:original", TITLE_TOKENS)
    assert rs.match("ja") == ["eng"]
    assert rs.match("zh") == ["fra"]


def test_language_before_covering_script_is_fine():
    # Specific first, broader after -> OK.
    rs = parse_inline("jpn:eng;cjk:fra", TITLE_TOKENS)
    assert rs.match("ja") == ["eng"]
    assert rs.match("zh") == ["fra"]


def test_default_covers_everything_after_errors():
    with pytest.raises(RuleError):
        parse_inline("default:eng;cjk:fra", TITLE_TOKENS)


# --- YAML ------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None,
    reason="PyYAML not installed",
)
def test_yaml_list_form_preserves_order():
    text = """
rules:
  - kana: [eng]
  - cjk: [fra]
  - default: [original]
"""
    rs = parse_yaml(text, TITLE_TOKENS)
    assert rs.match("ja") == ["eng"]
    assert rs.match("zh") == ["fra"]
    assert rs.match("en") == ["original"]


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None,
    reason="PyYAML not installed",
)
def test_yaml_list_form_catches_unreachable():
    text = """
rules:
  - cjk: [eng]
  - kana: [fra]
"""
    with pytest.raises(RuleError, match="Unreachable"):
        parse_yaml(text, TITLE_TOKENS)


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None,
    reason="PyYAML not installed",
)
def test_yaml_off_token_not_coerced_to_bool():
    # YAML parses bare `off` as the boolean False. The 'off' subtitle token must
    # survive that round-trip rather than becoming "false".
    text = """
rules:
  - eng: [off]
  - default: [off]
"""
    rs = parse_yaml(text, SUBTITLE_TOKENS)
    assert rs.match("eng") == ["off"]
    assert rs.match("jpn") == ["off"]


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None,
    reason="PyYAML not installed",
)
def test_yaml_off_mixed_with_language():
    text = """
rules:
  - eng: [fre, off]
"""
    rs = parse_yaml(text, SUBTITLE_TOKENS)
    assert rs.match("eng") == ["fra", "off"]

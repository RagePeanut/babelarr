"""Tests for the pure selection logic and config parsing.

These cover the behaviour we agreed on, especially the subtitle edge cases:
- English audio with no French subs -> subtitles OFF (no English fallback).
- An explicit rule never falls through to default.
- Audio channel threshold picks the best track at or below the cap.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lingarr.config import (  # noqa: E402
    load_subtitle_rules,
    parse_subtitle_rules_inline,
    parse_subtitle_rules_yaml,
)
from lingarr.langcodes import normalize, same_language  # noqa: E402
from lingarr.selector import (  # noqa: E402
    AudioStreamView,
    SubtitleStreamView,
    select_audio,
    select_for_part,
    select_subtitle,
)


# --- normalization ---------------------------------------------------------

def test_normalize_variants():
    assert normalize("fr") == normalize("fre") == normalize("fra") == "fra"
    assert normalize("en") == normalize("eng") == "eng"
    assert normalize("de") == normalize("ger") == normalize("deu") == "deu"
    assert normalize("und") is None
    assert normalize("") is None
    assert normalize(None) is None


def test_same_language():
    assert same_language("fr", "fra")
    assert same_language("eng", "en")
    assert not same_language("en", "fr")
    assert not same_language("en", None)


# --- rules parsing ---------------------------------------------------------

def test_inline_rules():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    assert rules.rules[normalize("eng")] == [normalize("fre")]
    assert rules.rules[normalize("fre")] == []
    assert rules.default == [normalize("fre"), normalize("eng")]


import pytest  # noqa: E402


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None,
    reason="PyYAML not installed in this environment",
)
def test_yaml_rules():
    text = """
rules:
  eng: [fre]
  fre: []
  default: [fre, eng]
"""
    rules = parse_subtitle_rules_yaml(text)
    assert rules.rules[normalize("eng")] == [normalize("fre")]
    assert rules.rules[normalize("fre")] == []
    assert rules.default == [normalize("fre"), normalize("eng")]


def test_empty_rules_leaves_untouched():
    assert load_subtitle_rules(None).is_empty()
    assert load_subtitle_rules("").is_empty()


# --- audio selection -------------------------------------------------------

def _audio(id, lang, ch, codec="ac3"):
    return AudioStreamView(id=id, language_code=lang, channels=ch, codec=codec)


def test_audio_picks_original_language_highest_channels():
    audios = [
        _audio(1, "eng", 6),
        _audio(2, "jpn", 2),
        _audio(3, "jpn", 6),
    ]
    assert select_audio(audios, "ja", None) == 3


def test_audio_channel_cap():
    audios = [
        _audio(1, "jpn", 2),
        _audio(2, "jpn", 6),
        _audio(3, "jpn", 8),
    ]
    # Cap at 6 -> pick the 5.1 track, not the 7.1.
    assert select_audio(audios, "ja", 6) == 2


def test_audio_cap_excludes_all_falls_back_to_lowest():
    audios = [_audio(1, "jpn", 8), _audio(2, "jpn", 6)]
    # Cap below everything -> lowest matching track.
    assert select_audio(audios, "ja", 2) == 2


def test_audio_no_match_returns_none():
    audios = [_audio(1, "eng", 6)]
    assert select_audio(audios, "ko", None) is None


def test_audio_codec_tiebreak():
    audios = [
        _audio(1, "eng", 6, codec="ac3"),
        _audio(2, "eng", 6, codec="truehd"),
    ]
    assert select_audio(audios, "en", None) == 2


# --- subtitle selection ----------------------------------------------------

def _sub(id, lang, forced=False):
    return SubtitleStreamView(id=id, language_code=lang, forced=forced)


def test_english_audio_french_subs_present():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id == 10
    assert not sel.disable_subtitles


def test_english_audio_no_french_subs_turns_off_no_english_fallback():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    subs = [_sub(11, "eng")]  # only English subs available
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id is None
    assert sel.disable_subtitles  # OFF, does NOT fall through to default


def test_french_audio_disables_subs():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_subtitle(subs, "fre", rules)
    assert sel.disable_subtitles


def test_other_audio_uses_default_french_then_english():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    # Japanese audio, no French subs -> English via default.
    subs = [_sub(11, "eng")]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id == 11


def test_other_audio_default_prefers_french():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id == 10


def test_other_audio_default_no_subs_available_off():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    subs = [_sub(12, "spa")]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.disable_subtitles


def test_no_rules_leaves_subs_untouched():
    rules = load_subtitle_rules(None)
    subs = [_sub(10, "fre")]
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id is None
    assert not sel.disable_subtitles  # untouched, NOT disabled


def test_prefers_non_forced_subtitle():
    rules = parse_subtitle_rules_inline("eng:fre")
    subs = [_sub(10, "fre", forced=True), _sub(11, "fre", forced=False)]
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id == 11


# --- full part selection ---------------------------------------------------

def test_select_for_part_japanese_movie():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    audios = [_audio(1, "jpn", 6), _audio(2, "eng", 6)]
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_for_part(audios, subs, "ja", rules, max_channels=None)
    assert sel.audio_stream_id == 1       # Japanese audio
    assert sel.subtitle_stream_id == 10   # French subs via default


def test_select_for_part_english_movie_no_french_subs():
    rules = parse_subtitle_rules_inline("eng:fre;fre:;default:fre,eng")
    audios = [_audio(1, "eng", 6)]
    subs = [_sub(11, "eng")]
    sel = select_for_part(audios, subs, "en", rules, max_channels=None)
    assert sel.audio_stream_id == 1
    assert sel.disable_subtitles          # subtitles OFF, no english over english

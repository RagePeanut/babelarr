"""Tests for audio + subtitle selection and language normalization."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from babelarr.config import SUBTITLE_TOKENS  # noqa: E402
from babelarr.langcodes import normalize, same_language  # noqa: E402
from babelarr.rules import parse_inline  # noqa: E402
from babelarr.selector import (  # noqa: E402
    AudioStreamView,
    SubtitleStreamView,
    select_audio,
    select_for_part,
    select_subtitle,
)


def _subs(value):
    return parse_inline(value, SUBTITLE_TOKENS)


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


# --- audio selection -------------------------------------------------------

def _audio(id, lang, ch, codec="ac3"):
    return AudioStreamView(id=id, language_code=lang, channels=ch, codec=codec)


def test_audio_picks_original_language_highest_channels():
    audios = [_audio(1, "eng", 6), _audio(2, "jpn", 2), _audio(3, "jpn", 6)]
    assert select_audio(audios, "ja", None) == 3


def test_audio_channel_cap():
    audios = [_audio(1, "jpn", 2), _audio(2, "jpn", 6), _audio(3, "jpn", 8)]
    assert select_audio(audios, "ja", 6) == 2


def test_audio_cap_excludes_all_falls_back_to_lowest():
    audios = [_audio(1, "jpn", 8), _audio(2, "jpn", 6)]
    assert select_audio(audios, "ja", 2) == 2


def test_audio_no_match_returns_none():
    assert select_audio([_audio(1, "eng", 6)], "ko", None) is None


def test_audio_codec_tiebreak():
    audios = [_audio(1, "eng", 6, codec="ac3"), _audio(2, "eng", 6, codec="truehd")]
    assert select_audio(audios, "en", None) == 2


# --- subtitle selection ----------------------------------------------------

def _sub(id, lang, forced=False):
    return SubtitleStreamView(id=id, language_code=lang, forced=forced)


def test_english_audio_french_subs_present():
    rules = _subs("eng:fre;default:fre,eng")
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_subtitle(subs, "eng", "eng", rules)
    assert sel.subtitle_stream_id == 10
    assert not sel.disable_subtitles


def test_matched_rule_but_missing_leaves_untouched():
    # eng audio wants French subs; only English present -> untouched (NOT off,
    # NOT fallthrough to default).
    rules = _subs("eng:fre;default:fre,eng")
    subs = [_sub(11, "eng")]
    sel = select_subtitle(subs, "eng", "eng", rules)
    assert sel.subtitle_stream_id is None
    assert not sel.disable_subtitles


def test_off_token_forces_subtitles_off():
    rules = _subs("fre:off;default:fre,eng")
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_subtitle(subs, "fre", "fre", rules)
    assert sel.disable_subtitles


def test_no_match_leaves_untouched():
    # No rule for 'jpn' and no default -> untouched.
    rules = _subs("eng:fre")
    subs = [_sub(10, "fre")]
    sel = select_subtitle(subs, "jpn", "jpn", rules)
    assert sel.subtitle_stream_id is None
    assert not sel.disable_subtitles


def test_original_token_subtitles():
    rules = _subs("eng:original")
    subs = [_sub(10, "jpn"), _sub(11, "eng")]
    # eng audio, original language japanese -> pick japanese subs
    sel = select_subtitle(subs, "eng", "jpn", rules)
    assert sel.subtitle_stream_id == 10


def test_default_used_for_other_audio():
    rules = _subs("eng:fre;default:fre,eng")
    subs = [_sub(11, "eng")]
    sel = select_subtitle(subs, "jpn", "jpn", rules)
    assert sel.subtitle_stream_id == 11


def test_prefers_non_forced_subtitle():
    rules = _subs("eng:fre")
    subs = [_sub(10, "fre", forced=True), _sub(11, "fre", forced=False)]
    sel = select_subtitle(subs, "eng", "eng", rules)
    assert sel.subtitle_stream_id == 11


# --- full part selection ---------------------------------------------------

def test_select_for_part_japanese_movie():
    rules = _subs("eng:fre;default:fre,eng")
    audios = [_audio(1, "jpn", 6), _audio(2, "eng", 6)]
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_for_part(audios, subs, "ja", rules, max_channels=None)
    assert sel.audio_stream_id == 1
    assert sel.subtitle_stream_id == 10  # French via default


def test_select_for_part_english_movie_off_rule():
    rules = _subs("eng:off;default:fre,eng")
    audios = [_audio(1, "eng", 6)]
    subs = [_sub(11, "eng")]
    sel = select_for_part(audios, subs, "en", rules, max_channels=None)
    assert sel.audio_stream_id == 1
    assert sel.disable_subtitles

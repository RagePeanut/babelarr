"""Tests for audio + subtitle selection and language normalization."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from babelarr.config import AUDIO_TOKENS, SUBTITLE_TOKENS  # noqa: E402
from babelarr.langcodes import normalize, same_language  # noqa: E402
from babelarr.rules import RuleSet, parse_inline  # noqa: E402
from babelarr.selector import (  # noqa: E402
    AudioStreamView,
    SubtitleStreamView,
    select_audio,
    select_for_part,
    select_subtitle,
)


def _subs(value):
    return parse_inline(value, SUBTITLE_TOKENS)


def _audio_rules(value):
    return parse_inline(value, AUDIO_TOKENS)


# OV preset: always original-language audio.
_OV = parse_inline("default:original", AUDIO_TOKENS)


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

def _audio(id, lang, ch, codec="ac3", default=False):
    return AudioStreamView(
        id=id, language_code=lang, channels=ch, codec=codec, is_default=default
    )


def test_audio_ov_picks_original_language_highest_channels():
    audios = [_audio(1, "eng", 6), _audio(2, "jpn", 2), _audio(3, "jpn", 6)]
    assert select_audio(audios, "ja", _OV, None) == 3


def test_audio_channel_cap():
    audios = [_audio(1, "jpn", 2), _audio(2, "jpn", 6), _audio(3, "jpn", 8)]
    assert select_audio(audios, "ja", _OV, 6) == 2


def test_audio_cap_excludes_all_falls_back_to_lowest():
    audios = [_audio(1, "jpn", 8), _audio(2, "jpn", 6)]
    assert select_audio(audios, "ja", _OV, 2) == 2


def test_audio_no_match_returns_none():
    assert select_audio([_audio(1, "eng", 6)], "ko", _OV, None) is None


def test_audio_silent_content_leaves_untouched():
    # Content language is "xx" (silent) and no xx audio track exists -> None.
    audios = [_audio(1, "eng", 6), _audio(2, "deu", 6)]
    assert select_audio(audios, "xx", _OV, None) is None


def test_audio_silent_content_selects_xx_track_if_present():
    # A real no-language audio track IS a valid pick for silent content.
    audios = [_audio(1, "eng", 6), _audio(2, "xx", 2)]
    assert select_audio(audios, "xx", _OV, None) == 2


def test_audio_codec_tiebreak():
    audios = [_audio(1, "eng", 6, codec="ac3"), _audio(2, "eng", 6, codec="truehd")]
    assert select_audio(audios, "en", _OV, None) == 2


def test_audio_no_rules_leaves_untouched():
    audios = [_audio(1, "jpn", 6)]
    assert select_audio(audios, "ja", RuleSet(), None) is None


def test_audio_native_dub_preference():
    # Watcher wants everything in German; English film has a German dub.
    rules = _audio_rules("default:deu")
    audios = [_audio(1, "eng", 6), _audio(2, "deu", 6)]
    assert select_audio(audios, "en", rules, None) == 2


def test_audio_rule_fallback_to_original():
    # Prefer German dub, else the original-language track.
    rules = _audio_rules("default:deu,original")
    audios = [_audio(1, "jpn", 6), _audio(2, "eng", 6)]  # no German
    assert select_audio(audios, "ja", rules, None) == 1  # original (jpn)


def test_audio_by_original_language_rule():
    # Japanese films in Japanese; everything else in English.
    rules = _audio_rules("jpn:jpn;default:eng")
    jp = [_audio(1, "jpn", 6), _audio(2, "eng", 6)]
    fr = [_audio(3, "fra", 6), _audio(4, "eng", 6)]
    assert select_audio(jp, "ja", rules, None) == 1
    assert select_audio(fr, "fr", rules, None) == 4


def test_audio_matched_but_absent_untouched():
    rules = _audio_rules("jpn:jpn")  # no default
    audios = [_audio(1, "eng", 6)]  # no Japanese track
    assert select_audio(audios, "ja", rules, None) is None


# --- subtitle selection ----------------------------------------------------

def _sub(id, lang, forced=False, codec=None):
    return SubtitleStreamView(id=id, language_code=lang, forced=forced, codec=codec)


# --- subtitle codec priority -----------------------------------------------

def test_codec_priority_picks_preferred_format():
    rules = _subs("default:fre")
    subs = [_sub(10, "fre", codec="pgs"), _sub(11, "fre", codec="srt")]
    sel = select_subtitle(subs, "eng", rules, format_priority=["srt", "pgs"])
    assert sel.subtitle_stream_id == 11  # srt beats pgs


def test_codec_priority_unlisted_sorts_last():
    rules = _subs("default:fre")
    # srt listed, ass/pgs not -> srt wins even though it is last in the file.
    subs = [_sub(10, "fre", codec="ass"), _sub(11, "fre", codec="srt")]
    sel = select_subtitle(subs, "eng", rules, format_priority=["srt"])
    assert sel.subtitle_stream_id == 11


def test_codec_priority_all_unlisted_first_wins():
    rules = _subs("default:fre")
    subs = [_sub(10, "fre", codec="ass"), _sub(11, "fre", codec="pgs")]
    sel = select_subtitle(subs, "eng", rules, format_priority=["srt"])
    assert sel.subtitle_stream_id == 10  # both unlisted -> first encountered


def test_codec_priority_unset_keeps_first_encountered():
    rules = _subs("default:fre")
    subs = [_sub(10, "fre", codec="pgs"), _sub(11, "fre", codec="srt")]
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id == 10  # no priority -> historical behavior


def test_english_audio_french_subs_present():
    rules = _subs("eng:fre;default:fre,eng")
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id == 10
    assert not sel.disable_subtitles


def test_matched_rule_but_missing_leaves_untouched():
    # eng audio wants French subs; only English present -> untouched (NOT off,
    # NOT fallthrough to default).
    rules = _subs("eng:fre;default:fre,eng")
    subs = [_sub(11, "eng")]
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id is None
    assert not sel.disable_subtitles


def test_off_token_forces_subtitles_off():
    rules = _subs("fre:off;default:fre,eng")
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_subtitle(subs, "fre", rules)
    assert sel.disable_subtitles


def test_no_match_leaves_untouched():
    # No rule for 'jpn' and no default -> untouched.
    rules = _subs("eng:fre")
    subs = [_sub(10, "fre")]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id is None
    assert not sel.disable_subtitles


def test_original_token_rejected_for_subtitles():
    import pytest
    from babelarr.rules import RuleError
    with pytest.raises(RuleError):
        _subs("eng:original")


def test_default_used_for_other_audio():
    rules = _subs("eng:fre;default:fre,eng")
    subs = [_sub(11, "eng")]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id == 11


def test_prefers_non_forced_subtitle():
    rules = _subs("eng:fre")
    subs = [_sub(10, "fre", forced=True), _sub(11, "fre", forced=False)]
    sel = select_subtitle(subs, "eng", rules)
    assert sel.subtitle_stream_id == 11


# --- forced-subtitle preference (<lang>-forced) ----------------------------

def _subsf(value):
    """Subtitle rules with the ``<lang>-forced`` modifier enabled."""
    return parse_inline(value, SUBTITLE_TOKENS, allow_forced=True)


def test_forced_pref_selects_forced_track_in_that_language():
    # Canonical case: jpn audio -> full fr, else forced fr, else off.
    rules = _subsf("jpn:fre,fre-forced,off")
    # Only a forced French track exists -> the fre-forced pref catches it.
    subs = [_sub(10, "fre", forced=True), _sub(11, "eng", forced=False)]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id == 10
    assert not sel.disable_subtitles


def test_full_pref_wins_over_forced_when_both_present():
    # With a full fr track present, bare 'fre' matches first; fre-forced unused.
    rules = _subsf("jpn:fre,fre-forced,off")
    subs = [_sub(10, "fre", forced=True), _sub(11, "fre", forced=False)]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id == 11
    assert not sel.disable_subtitles


def test_forced_pref_falls_through_to_off_when_no_forced_track():
    # fre full absent, and the only fr track is NOT forced -> fre matches it
    # (bare fre), so we never reach off. Guards against over-eager disabling.
    rules = _subsf("jpn:fre-forced,off")
    subs = [_sub(11, "fre", forced=False)]
    sel = select_subtitle(subs, "jpn", rules)
    # fre-forced requires a forced track; the non-forced one doesn't qualify ->
    # fall through to off.
    assert sel.subtitle_stream_id is None
    assert sel.disable_subtitles


def test_forced_only_rule_with_no_fr_at_all_falls_to_off():
    rules = _subsf("jpn:fre-forced,off")
    subs = [_sub(11, "eng", forced=False)]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.disable_subtitles


def test_forced_language_independent_of_audio():
    # Forced subs in MY language (eng) regardless of the jpn audio playing.
    rules = _subsf("jpn:eng-forced,off")
    subs = [_sub(20, "eng", forced=True), _sub(21, "fre", forced=False)]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id == 20


def test_forced_pref_ignores_non_forced_of_same_language():
    # eng-forced must NOT grab a non-forced eng track; it should skip to next.
    rules = _subsf("jpn:eng-forced,fre")
    subs = [_sub(20, "eng", forced=False), _sub(21, "fre", forced=False)]
    sel = select_subtitle(subs, "jpn", rules)
    assert sel.subtitle_stream_id == 21  # fell through to fre


# --- full part selection ---------------------------------------------------

def test_select_for_part_japanese_movie_ov():
    subs_rules = _subs("eng:fre;default:fre,eng")
    audios = [_audio(1, "jpn", 6), _audio(2, "eng", 6)]
    subs = [_sub(10, "fre"), _sub(11, "eng")]
    sel = select_for_part(audios, subs, "ja", _OV, subs_rules, max_channels=None)
    assert sel.audio_stream_id == 1  # Japanese (original)
    assert sel.subtitle_stream_id == 10  # French via default (jpn audio)


def test_select_for_part_english_movie_off_rule():
    subs_rules = _subs("eng:off;default:fre,eng")
    audios = [_audio(1, "eng", 6)]
    subs = [_sub(11, "eng")]
    sel = select_for_part(audios, subs, "en", _OV, subs_rules, max_channels=None)
    assert sel.audio_stream_id == 1
    assert sel.disable_subtitles


def test_select_for_part_subtitles_key_off_chosen_dub():
    # Audio rule dubs a Japanese film to English; subtitle rule for 'eng' audio
    # should then apply (NOT the rule for the original 'jpn').
    audio_rules = _audio_rules("default:eng")
    subs_rules = _subs("eng:fre;jpn:off")
    audios = [_audio(1, "jpn", 6), _audio(2, "eng", 6)]
    subs = [_sub(10, "fre")]
    sel = select_for_part(audios, subs, "ja", audio_rules, subs_rules, max_channels=None)
    assert sel.audio_stream_id == 2  # English dub chosen
    assert sel.subtitle_stream_id == 10  # French subs (eng-audio rule)
    assert not sel.disable_subtitles


def test_select_for_part_audio_untouched_keys_on_current_default():
    # No audio rules -> audio untouched. Subtitle keying uses the current
    # default audio track's language, not the original language.
    subs_rules = _subs("eng:fre;jpn:off")
    audios = [_audio(1, "jpn", 6, default=True), _audio(2, "eng", 6)]
    subs = [_sub(10, "fre")]
    sel = select_for_part(audios, subs, "ja", RuleSet(), subs_rules, max_channels=None)
    assert sel.audio_stream_id is None  # untouched
    assert sel.disable_subtitles  # jpn default audio -> jpn:off rule

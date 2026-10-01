"""Pure audio/subtitle track-selection logic.

Kept free of Plex/network dependencies so it is easy to reason about and unit
test. Callers pass lightweight views of a title's streams plus the title's
original language, and get back which audio/subtitle stream ids to set as
default.

Audio semantics (Babelarr grammar):
  * Driven by ``AUDIO_RULES`` keyed on the title's original language.
  * Preferences are languages; ``original`` resolves to the original language.
  * Within the chosen language, the best track is picked (most channels not
    exceeding ``MAX_AUDIO_CHANNELS``; codec rank breaks ties).
  * No rule / no match / matched-but-absent -> leave audio untouched. (No
    ``off`` token: audio is never "disabled".)

Subtitle semantics (Babelarr grammar):
  * No rule matches (and no ``default``)      -> leave subtitles untouched.
  * Matched rule's preferences, first available wins.
  * ``off`` token in a matched rule           -> force subtitles OFF.
  * ``original`` token                         -> original-language subtitle.
  * Matched rule but none of its preferences present -> leave untouched
    (let Plex handle it); does NOT fall through to ``default``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .langcodes import normalize
from .rules import RuleSet, TOKEN_OFF, TOKEN_ORIGINAL


@dataclass(frozen=True)
class AudioStreamView:
    id: int
    language_code: Optional[str]
    channels: int  # audioChannels; 0/unknown treated as lowest
    codec: Optional[str] = None
    is_default: bool = False


@dataclass(frozen=True)
class SubtitleStreamView:
    id: int
    language_code: Optional[str]
    is_default: bool = False
    forced: bool = False


@dataclass(frozen=True)
class Selection:
    """Result of selecting tracks for one media part.

    ``audio_stream_id`` / ``subtitle_stream_id`` are ids to make default;
    ``None`` means "leave that dimension's current default alone".
    ``disable_subtitles`` set means explicitly turn subtitles OFF.
    """

    audio_stream_id: Optional[int]
    subtitle_stream_id: Optional[int]
    disable_subtitles: bool


_CODEC_RANK = {
    "truehd": 60, "dtshd": 55, "dts-hd": 55, "flac": 50, "pcm": 50,
    "lpcm": 50, "dts": 40, "eac3": 30, "ac3": 20, "aac": 15, "mp3": 10,
    "opus": 12, "vorbis": 8,
}


def _codec_rank(codec: Optional[str]) -> int:
    if not codec:
        return 0
    return _CODEC_RANK.get(codec.strip().lower(), 0)


def _best_track_in_language(
    audios: Sequence[AudioStreamView],
    language: Optional[str],
    max_channels: Optional[int],
) -> Optional[int]:
    """Best audio stream id in a given language, honoring the channel cap.

    Picks the most channels not exceeding ``max_channels`` (codec rank breaks
    ties). If the cap excludes every matching track, falls back to the
    lowest-channel matching track. Returns ``None`` if no track matches.
    """
    lang = normalize(language)
    if lang is None:
        return None
    matching = [a for a in audios if normalize(a.language_code) == lang]
    if not matching:
        return None
    capped = [a for a in matching if max_channels is None or a.channels <= max_channels]
    pool = capped if capped else sorted(matching, key=lambda a: a.channels)[:1]
    best = max(pool, key=lambda a: (a.channels, _codec_rank(a.codec)))
    return best.id


def select_audio(
    audios: Sequence[AudioStreamView],
    original_language: Optional[str],
    rules: RuleSet,
    max_channels: Optional[int],
) -> Optional[int]:
    """Choose the audio stream id according to ``AUDIO_RULES``.

    The first rule matching the title's original language is authoritative; its
    preferences are tried in order (``original`` resolves to the original
    language), and for each the best available track in that language is chosen
    (see ``_best_track_in_language``). Returns ``None`` to leave audio untouched
    when no rule matches, or when a matched rule's preferences are all absent.
    """
    prefs = rules.match(original_language)
    if prefs is None:
        return None  # no AUDIO_RULES / no match -> leave audio untouched

    for want in prefs:
        want_lang = original_language if want == TOKEN_ORIGINAL else want
        chosen = _best_track_in_language(audios, want_lang, max_channels)
        if chosen is not None:
            return chosen

    return None  # matched rule, nothing available -> untouched


def select_subtitle(
    subtitles: Sequence[SubtitleStreamView],
    audio_language: Optional[str],
    original_language: Optional[str],
    rules: RuleSet,
) -> Selection:
    """Decide the subtitle stream given the chosen audio language and rules.

    The rule is matched on the audio language that will actually play. Each
    preference is resolved against available subtitle streams; ``off`` forces
    subtitles off; ``original`` resolves to the title's original language.
    """
    prefs = rules.match(audio_language)
    if prefs is None:
        return Selection(None, None, disable_subtitles=False)  # untouched

    for want in prefs:
        if want == TOKEN_OFF:
            return Selection(None, None, disable_subtitles=True)
        want_lang = original_language if want == TOKEN_ORIGINAL else want
        want_norm = normalize(want_lang)
        if want_norm is None:
            continue
        candidates = [s for s in subtitles if normalize(s.language_code) == want_norm]
        if not candidates:
            continue
        non_forced = [s for s in candidates if not s.forced]
        chosen = non_forced[0] if non_forced else candidates[0]
        return Selection(None, chosen.id, disable_subtitles=False)

    # Matched rule but nothing available -> leave untouched (Plex handles it).
    return Selection(None, None, disable_subtitles=False)


def select_for_part(
    audios: Sequence[AudioStreamView],
    subtitles: Sequence[SubtitleStreamView],
    original_language: Optional[str],
    audio_rules: RuleSet,
    subtitle_rules: RuleSet,
    max_channels: Optional[int],
) -> Selection:
    """Full audio+subtitle selection for a single media part.

    Audio is chosen first (per ``AUDIO_RULES``). Subtitles are then keyed on the
    audio language that will **actually play**: the track Babelarr selected if
    it changed anything, otherwise the track Plex currently defaults to (falling
    back to the original language only if no default is marked).
    """
    audio_id = select_audio(audios, original_language, audio_rules, max_channels)

    if audio_id is not None:
        playing_lang = next(
            (a.language_code for a in audios if a.id == audio_id), original_language
        )
    else:
        # Audio left untouched: whatever Plex currently defaults to will play.
        playing_lang = next(
            (a.language_code for a in audios if a.is_default), None
        )
        if playing_lang is None:
            playing_lang = original_language

    sub_sel = select_subtitle(subtitles, playing_lang, original_language, subtitle_rules)
    return Selection(
        audio_stream_id=audio_id,
        subtitle_stream_id=sub_sel.subtitle_stream_id,
        disable_subtitles=sub_sel.disable_subtitles,
    )

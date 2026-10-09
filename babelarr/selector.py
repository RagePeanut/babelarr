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
  * Matched rule but none of its preferences present -> leave untouched
    (let Plex handle it); does NOT fall through to ``default``.
  * No ``original`` token: rules key on the played audio, so concrete language
    keys plus ``default`` already cover every case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .langcodes import is_no_language, normalize
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
    codec: Optional[str] = None  # subtitle format: srt, ass, pgs, vobsub, …


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
    if is_no_language(language):
        # Silent/no-dialogue content: match an explicitly no-language audio
        # track if one exists (rare), else nothing -> audio left untouched.
        matching = [a for a in audios if is_no_language(a.language_code)]
    else:
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
        want_lang = original_language if want == TOKEN_ORIGINAL else want.token
        chosen = _best_track_in_language(audios, want_lang, max_channels)
        if chosen is not None:
            return chosen

    return None  # matched rule, nothing available -> untouched


def _format_rank(codec: Optional[str], format_priority: Sequence[str]) -> int:
    """Position of a subtitle's format in ``format_priority`` (lower = better).

    Formats not listed sort *after* every listed one; among equally-ranked
    tracks the original (first-encountered) order is preserved by the caller's
    stable sort, so "the first encountered wins".
    """
    if not format_priority:
        return 0
    norm = (codec or "").strip().lower()
    try:
        return format_priority.index(norm)
    except ValueError:
        return len(format_priority)


def _prefer_by_format(
    candidates: Sequence[SubtitleStreamView], format_priority: Sequence[str]
) -> List[SubtitleStreamView]:
    """Stable-sort subtitle candidates by format priority (no-op if unset)."""
    if not format_priority:
        return list(candidates)
    return sorted(candidates, key=lambda s: _format_rank(s.codec, format_priority))


def select_subtitle(
    subtitles: Sequence[SubtitleStreamView],
    audio_language: Optional[str],
    rules: RuleSet,
    format_priority: Optional[Sequence[str]] = None,
) -> Selection:
    """Decide the subtitle stream given the chosen audio language and rules.

    The rule is matched on the audio language that will actually play. Each
    preference is a concrete subtitle language; ``off`` forces subtitles off.
    (Subtitles have no ``original`` token: with rules keyed on the played audio,
    concrete language keys plus ``default`` already cover every case.)

    When several tracks tie on language (and forced-ness), ``format_priority``
    -- an ordered list of subtitle formats (``srt``, ``ass``, ``pgs``, …) --
    breaks the tie: earlier formats win, unlisted formats come last, and ties
    among unlisted formats keep the first-encountered track.
    """
    format_priority = format_priority or []
    prefs = rules.match(audio_language)
    if prefs is None:
        return Selection(None, None, disable_subtitles=False)  # untouched

    for want in prefs:
        if want == TOKEN_OFF:
            return Selection(None, None, disable_subtitles=True)
        want_norm = normalize(want.token)
        if want_norm is None:
            continue
        candidates = [s for s in subtitles if normalize(s.language_code) == want_norm]
        if not candidates:
            continue
        if want.forced:
            # ``<lang>-forced``: match ONLY a forced track in this language. If
            # none is forced, this preference doesn't apply -> try the next one.
            forced = [s for s in candidates if s.forced]
            if not forced:
                continue
            chosen = _prefer_by_format(forced, format_priority)[0]
        else:
            # Bare ``<lang>``: prefer a full/non-forced track, fall back to a
            # forced one only if that's all there is (historical behavior).
            non_forced = [s for s in candidates if not s.forced]
            pool = non_forced if non_forced else candidates
            chosen = _prefer_by_format(pool, format_priority)[0]
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
    subtitle_format_priority: Optional[Sequence[str]] = None,
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

    sub_sel = select_subtitle(
        subtitles, playing_lang, subtitle_rules, subtitle_format_priority
    )
    return Selection(
        audio_stream_id=audio_id,
        subtitle_stream_id=sub_sel.subtitle_stream_id,
        disable_subtitles=sub_sel.disable_subtitles,
    )

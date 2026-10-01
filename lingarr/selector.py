"""Pure track-selection logic.

Kept free of Plex/network dependencies so it is easy to reason about and unit
test. Callers pass in lightweight views of a title's streams plus the title's
original language, and get back which audio/subtitle stream ids to set as
default (or ``None`` meaning "do not change this dimension").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .config import SubtitleRules
from .langcodes import normalize


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

    ``audio_stream_id`` / ``subtitle_stream_id`` are the ids to make default.
    ``None`` for audio means "leave current audio default".
    ``subtitle_stream_id is None`` combined with ``disable_subtitles`` tells the
    caller to turn subtitles off; ``None`` without the disable flag means
    "leave subtitles untouched".
    """

    audio_stream_id: Optional[int]
    subtitle_stream_id: Optional[int]
    disable_subtitles: bool


# Rough codec quality ranking used only as a tie-breaker when channel counts
# are equal. Higher is better.
_CODEC_RANK = {
    "truehd": 60,
    "dtshd": 55,
    "dts-hd": 55,
    "flac": 50,
    "pcm": 50,
    "lpcm": 50,
    "dts": 40,
    "eac3": 30,
    "ac3": 20,
    "aac": 15,
    "mp3": 10,
    "opus": 12,
    "vorbis": 8,
}


def _codec_rank(codec: Optional[str]) -> int:
    if not codec:
        return 0
    return _CODEC_RANK.get(codec.strip().lower(), 0)


def select_audio(
    audios: Sequence[AudioStreamView],
    original_language: Optional[str],
    max_channels: Optional[int],
) -> Optional[int]:
    """Choose the audio stream id matching the original language.

    Among tracks whose language matches ``original_language``, pick the one with
    the most channels that does not exceed ``max_channels`` (if set). Codec rank
    then bitrate-agnostic stability break ties. Returns ``None`` if no track
    matches the original language (caller leaves audio untouched).
    """
    orig = normalize(original_language)
    if orig is None:
        return None

    matching = [a for a in audios if normalize(a.language_code) == orig]
    if not matching:
        return None

    capped = [
        a
        for a in matching
        if max_channels is None or a.channels <= max_channels
    ]
    # If the cap excludes everything (every matching track is above the ceiling),
    # fall back to the lowest-channel matching track rather than giving up.
    pool = capped if capped else sorted(matching, key=lambda a: a.channels)[:1]

    best = max(pool, key=lambda a: (a.channels, _codec_rank(a.codec)))
    return best.id


def select_subtitle(
    subtitles: Sequence[SubtitleStreamView],
    audio_language: Optional[str],
    rules: SubtitleRules,
) -> Selection:
    """Decide the subtitle stream given the chosen audio language and rules.

    Semantics:
    * No rule and no default -> leave subtitles untouched.
    * A matched rule's preferences are tried in order; first available wins.
    * If a rule exists but none of its preferences are present (including the
      empty-list "disable" case) -> subtitles OFF. Never falls through to
      default once an explicit rule matched.
    """
    prefs = rules.lookup(audio_language)
    if prefs is None:
        # Nothing configured -> don't touch subtitles.
        return Selection(audio_stream_id=None, subtitle_stream_id=None,
                         disable_subtitles=False)

    # Prefer non-forced full subtitles; fall back to forced if that's all there is.
    for want in prefs:
        want_norm = normalize(want)
        candidates = [
            s for s in subtitles if normalize(s.language_code) == want_norm
        ]
        if not candidates:
            continue
        non_forced = [s for s in candidates if not s.forced]
        chosen = non_forced[0] if non_forced else candidates[0]
        return Selection(audio_stream_id=None, subtitle_stream_id=chosen.id,
                         disable_subtitles=False)

    # Rule matched but no preferred subtitle exists -> turn subtitles off.
    return Selection(audio_stream_id=None, subtitle_stream_id=None,
                     disable_subtitles=True)


def select_for_part(
    audios: Sequence[AudioStreamView],
    subtitles: Sequence[SubtitleStreamView],
    original_language: Optional[str],
    rules: SubtitleRules,
    max_channels: Optional[int],
) -> Selection:
    """Full selection for a single media part."""
    audio_id = select_audio(audios, original_language, max_channels)

    # The audio language that will actually play drives the subtitle rule. If we
    # picked a track, use its language; otherwise use the original language as a
    # best effort (what Plex is most likely already defaulting to).
    audio_lang = original_language
    if audio_id is not None:
        for a in audios:
            if a.id == audio_id:
                audio_lang = a.language_code
                break

    sub_sel = select_subtitle(subtitles, audio_lang, rules)
    return Selection(
        audio_stream_id=audio_id,
        subtitle_stream_id=sub_sel.subtitle_stream_id,
        disable_subtitles=sub_sel.disable_subtitles,
    )

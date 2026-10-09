"""Tests for _process_tracks: episodes with no stream data get reloaded first.

Confirmed from a live server: episodes from show.episodes() have Media + Part
but NO <Stream> children -- streams only appear on the per-episode detail
endpoint (fetched by reload()). Without reloading, audioStreams()/
subtitleStreams() are empty and nothing is applied (the "works for movies, not
shows" bug). We trigger the reload by detecting a part with no streams, not by
trusting isFullObject().
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.processor import Processor  # noqa: E402
from babelarr.rules import parse_inline  # noqa: E402
from babelarr.config import AUDIO_TOKENS, SUBTITLE_TOKENS  # noqa: E402


class FakeStream:
    def __init__(self, id, lang, channels=6, codec="ac3", selected=False):
        self.id = id
        self.languageCode = lang
        self.audioChannels = channels
        self.codec = codec
        self.selected = selected
        self.default = selected
        self.forced = False


class FakePart:
    """A Part that starts with NO streams; gains them after the item reloads."""

    def __init__(self, audio, subs):
        self._audio_full = audio
        self._subs_full = subs
        self.has_streams = False  # listing state: empty
        self.selected_audio = None
        self.selected_sub = None

    def audioStreams(self):
        return self._audio_full if self.has_streams else []

    def subtitleStreams(self):
        return self._subs_full if self.has_streams else []

    def setSelectedAudioStream(self, s):
        self.selected_audio = s

    def setSelectedSubtitleStream(self, s):
        self.selected_sub = s

    def resetSelectedSubtitleStream(self):
        self.selected_sub = 0


class FakeMedia:
    def __init__(self, part):
        self.parts = [part]


class FakeEpisode:
    def __init__(self, part):
        self.title = "Ep1"
        self.ratingKey = "e1"
        self._part = part
        self.media = [FakeMedia(part)]
        self.reloaded = 0

    def reload(self):
        # Detail endpoint populates the streams.
        self.reloaded += 1
        self._part.has_streams = True


def _processor():
    p = Processor.__new__(Processor)
    class Cfg:
        audio_rules = parse_inline("default:original", AUDIO_TOKENS)
        subtitle_rules = parse_inline("jpn:off;default:fre", SUBTITLE_TOKENS)
        max_audio_channels = None
        subtitle_format_priority = []
        dry_run = False
    p.config = Cfg()
    return p


def test_episode_without_streams_is_reloaded_then_applied():
    # jpn content; after reload the file exposes jpn+eng audio and a selected
    # fre subtitle. Expect jpn audio chosen and jpn:off -> subs disabled.
    part = FakePart(
        audio=[FakeStream(1, "jpn"), FakeStream(2, "eng", selected=True)],
        subs=[FakeStream(10, "fre", selected=True)],
    )
    ep = FakeEpisode(part)
    p = _processor()

    p._process_tracks(ep, "jpn")

    assert ep.reloaded == 1           # no streams -> reloaded once
    assert part.selected_audio == 1   # Japanese audio chosen
    assert part.selected_sub == 0     # jpn:off disabled the on subtitle


def test_episode_with_streams_not_reloaded():
    part = FakePart(audio=[FakeStream(1, "jpn", selected=True)], subs=[])
    part.has_streams = True  # already populated (e.g. a movie-like item)
    ep = FakeEpisode(part)
    p = _processor()

    p._process_tracks(ep, "jpn")
    assert ep.reloaded == 0  # streams already present -> no reload

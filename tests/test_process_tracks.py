"""Tests for _process_tracks: partial items (episodes) are reloaded first.

Regression: episodes from show.episodes() come back partial -- their Parts have
no Stream children -- so without a reload the audio/subtitle lists are empty and
nothing is applied (the "works for movies, not shows" bug).
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
    def __init__(self, audio, subs):
        self._audio = audio
        self._subs = subs
        self.selected_audio = None
        self.selected_sub = None

    def audioStreams(self):
        return self._audio

    def subtitleStreams(self):
        return self._subs

    def setSelectedAudioStream(self, s):
        self.selected_audio = s

    def setSelectedSubtitleStream(self, s):
        self.selected_sub = s

    def resetSelectedSubtitleStream(self):
        self.selected_sub = 0


class FakeMedia:
    def __init__(self, part):
        self.parts = [part]


class PartialEpisode:
    """Starts partial (no media); reload() populates full media/parts."""

    def __init__(self, part):
        self.title = "Ep1"
        self.ratingKey = "e1"
        self._full = False
        self._part = part
        self.media = []  # empty until reloaded
        self.reloaded = 0

    def isFullObject(self):
        return self._full

    def reload(self):
        self.reloaded += 1
        self._full = True
        self.media = [FakeMedia(self._part)]


def _processor():
    p = Processor.__new__(Processor)
    class Cfg:
        audio_rules = parse_inline("default:original", AUDIO_TOKENS)
        subtitle_rules = parse_inline("jpn:off;default:fre", SUBTITLE_TOKENS)
        max_audio_channels = None
        dry_run = False
    p.config = Cfg()
    return p


def test_partial_episode_is_reloaded_then_tracks_applied():
    # Japanese content; file has jpn + eng audio. Expect jpn audio selected,
    # and 'jpn:off' -> subtitles disabled.
    part = FakePart(
        audio=[FakeStream(1, "jpn", selected=False),
               FakeStream(2, "eng", selected=True)],
        subs=[FakeStream(10, "fre", selected=True)],  # a sub is currently on
    )
    ep = PartialEpisode(part)
    p = _processor()

    p._process_tracks(ep, "jpn")

    assert ep.reloaded == 1               # partial -> reloaded
    assert part.selected_audio == 1       # Japanese audio chosen
    assert part.selected_sub == 0         # jpn:off -> currently-on sub disabled


def test_full_object_not_reloaded():
    part = FakePart(audio=[FakeStream(1, "jpn", selected=True)], subs=[])
    ep = PartialEpisode(part)
    ep._full = True
    ep.media = [FakeMedia(part)]  # already populated
    p = _processor()

    p._process_tracks(ep, "jpn")
    assert ep.reloaded == 0  # already full -> no reload

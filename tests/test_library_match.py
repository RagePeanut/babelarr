"""Tests for _target_sections: library filtering + not-found warnings."""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.processor import Processor  # noqa: E402


class FakeSection:
    def __init__(self, title, type_):
        self.title = title
        self.type = type_


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections
    def sections(self):
        return self._sections


class FakeServer:
    def __init__(self, sections):
        self.library = FakeLibrary(sections)


def _processor(sections, libraries):
    p = Processor.__new__(Processor)
    class Cfg:
        pass
    cfg = Cfg()
    cfg.libraries = libraries
    p.config = cfg
    p.server = FakeServer(sections)
    return p


def test_selects_only_wanted_types_and_names():
    sections = [
        FakeSection("Films", "movie"),
        FakeSection("Séries TV", "show"),
        FakeSection("Music", "artist"),  # ignored type
    ]
    p = _processor(sections, ["Films"])
    titles = [s.title for s in p._target_sections()]
    assert titles == ["Films"]


def test_all_movie_show_libraries_when_unset():
    sections = [
        FakeSection("Films", "movie"),
        FakeSection("Séries TV", "show"),
        FakeSection("Music", "artist"),
    ]
    p = _processor(sections, None)
    titles = sorted(s.title for s in p._target_sections())
    assert titles == ["Films", "Séries TV"]


def test_warns_on_unknown_library(caplog):
    sections = [FakeSection("Films", "movie"), FakeSection("Séries TV", "show")]
    p = _processor(sections, ["Films", "Typo Library"])
    with caplog.at_level(logging.WARNING, logger="babelarr.processor"):
        selected = p._target_sections()
    # Only the real one is selected...
    assert [s.title for s in selected] == ["Films"]
    # ...and the typo'd one produced a warning naming it.
    assert any("Typo Library" in r.message for r in caplog.records)


def test_no_warning_when_all_libraries_match(caplog):
    sections = [FakeSection("Films", "movie"), FakeSection("Séries TV", "show")]
    p = _processor(sections, ["Films", "Séries TV"])
    with caplog.at_level(logging.WARNING, logger="babelarr.processor"):
        p._target_sections()
    assert not any("PLEX_LIBRARIES" in r.message for r in caplog.records)

"""Tests for sweep resilience: retry-with-backoff and per-item isolation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import babelarr.processor as proc  # noqa: E402
from babelarr.processor import Processor, _with_retry  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Make backoff instant so tests are fast.
    monkeypatch.setattr(proc.time, "sleep", lambda *_: None)


# --- _with_retry ------------------------------------------------------------

def test_retry_returns_on_first_success():
    calls = []
    def fn():
        calls.append(1)
        return "ok"
    assert _with_retry(fn, 3, "thing") == "ok"
    assert len(calls) == 1


def test_retry_succeeds_after_transient_failures():
    calls = []
    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("boom")
        return "ok"
    assert _with_retry(fn, 3, "thing") == "ok"
    assert len(calls) == 3


def test_retry_reraises_after_exhausting_attempts():
    def fn():
        raise TimeoutError("always")
    with pytest.raises(TimeoutError):
        _with_retry(fn, 3, "thing")


# --- resilient full_sweep ---------------------------------------------------

class FakeSection:
    def __init__(self, title, type_, items=None, fail_times=0):
        self.title = title
        self.type = type_
        self._items = items or []
        self._fail_times = fail_times
        self.calls = 0

    def all(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise TimeoutError("enumeration timed out")
        return list(self._items)


class FakeState:
    def __init__(self):
        self.flushed = 0
    def flush(self):
        self.flushed += 1


class FakeItem:
    def __init__(self, title, boom=False):
        self.title = title
        self.ratingKey = title
        self._boom = boom


def _make_processor(sections):
    p = Processor.__new__(Processor)  # bypass __init__ (needs plex/tmdb)
    class Cfg:
        plex_retries = 3
    p.config = Cfg()
    p.state = FakeState()
    p._sections = sections
    p._target_sections = lambda: sections
    return p


def test_full_sweep_isolates_bad_items(monkeypatch):
    good1 = FakeItem("Good1")
    bad = FakeItem("Bad", boom=True)
    good2 = FakeItem("Good2")
    sec = FakeSection("Films", "movie", items=[good1, bad, good2])
    p = _make_processor([sec])

    processed = []
    def fake_process_movie(item):
        if getattr(item, "_boom", False):
            raise RuntimeError("bad item")
        processed.append(item.title)
    monkeypatch.setattr(p, "process_movie", fake_process_movie)

    p.full_sweep()
    # Bad item didn't abort the sweep; the good ones were processed.
    assert processed == ["Good1", "Good2"]
    assert p.state.flushed == 1


def test_full_sweep_retries_section_enumeration(monkeypatch):
    # Section times out twice, then succeeds -> items still processed.
    sec = FakeSection("Films", "movie", items=[FakeItem("A")], fail_times=2)
    p = _make_processor([sec])
    processed = []
    monkeypatch.setattr(p, "process_movie", lambda it: processed.append(it.title))

    p.full_sweep()
    assert processed == ["A"]
    assert sec.calls == 3  # 2 failures + 1 success


def test_full_sweep_skips_section_after_exhausting_retries(monkeypatch):
    bad_sec = FakeSection("Films", "movie", items=[FakeItem("A")], fail_times=99)
    good_sec = FakeSection("Shows", "show", items=[FakeItem("S")])
    p = _make_processor([bad_sec, good_sec])
    processed = []
    monkeypatch.setattr(p, "process_movie", lambda it: processed.append(it.title))
    monkeypatch.setattr(p, "process_show", lambda it: processed.append(it.title))

    p.full_sweep()
    # Bad section skipped after retries; the other section still ran.
    assert processed == ["S"]
    assert p.state.flushed == 1


def test_full_sweep_aborts_cleanly_if_sections_unavailable(monkeypatch):
    p = Processor.__new__(Processor)
    class Cfg:
        plex_retries = 2
    p.config = Cfg()
    p.state = FakeState()
    def boom():
        raise TimeoutError("cannot reach plex")
    p._target_sections = boom

    p.full_sweep()  # must not raise
    # Aborted before flush (nothing to persist).
    assert p.state.flushed == 0

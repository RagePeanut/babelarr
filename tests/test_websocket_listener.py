"""Tests for the Plex WebSocket dispatcher (NEW_MEDIA_MODE=websocket).

These exercise the pure message-filtering + debounce logic in
``WebSocketDispatcher`` without a real Plex WebSocket: we feed it the
``NotificationContainer`` dicts plexapi would hand our callback and assert which
ratingKeys get dispatched to the processor.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from babelarr.websocket_listener import WebSocketDispatcher  # noqa: E402


class FakeProcessor:
    """Records the ratingKeys handed to process_rating_key."""

    def __init__(self):
        self.calls = []
        self.event = threading.Event()

    def process_rating_key(self, rating_key):
        self.calls.append(rating_key)
        self.event.set()


def _timeline(item_id, state, identifier="com.plexapp.plugins.library"):
    return {
        "type": "timeline",
        "TimelineEntry": [
            {"identifier": identifier, "itemID": item_id, "state": state}
        ],
    }


def _make(processor, debounce=0.02):
    # Tiny debounce so tests stay fast but still exercise the timer path.
    return WebSocketDispatcher(processor, debounce_seconds=debounce)


def _wait(proc, timeout=1.0):
    assert proc.event.wait(timeout), "processor was never called"


def test_state_processed_dispatches_rating_key():
    proc = FakeProcessor()
    disp = _make(proc)
    disp.on_message(_timeline("42", 5))
    _wait(proc)
    assert proc.calls == [42]


def test_non_processed_states_are_ignored():
    proc = FakeProcessor()
    disp = _make(proc)
    for state in (0, 1, 2, 3, 4, 9):
        disp.on_message(_timeline("42", state))
    # Give any erroneously-scheduled timer a chance to fire.
    assert not proc.event.wait(0.1)
    assert proc.calls == []


def test_non_timeline_messages_are_ignored():
    proc = FakeProcessor()
    disp = _make(proc)
    disp.on_message({"type": "playing", "PlaySessionStateNotification": [{}]})
    disp.on_message({"type": "status"})
    assert not proc.event.wait(0.1)
    assert proc.calls == []


def test_other_identifier_is_ignored():
    proc = FakeProcessor()
    disp = _make(proc)
    disp.on_message(_timeline("42", 5, identifier="com.plexapp.plugins.other"))
    assert not proc.event.wait(0.1)
    assert proc.calls == []


def test_burst_is_debounced_to_single_call():
    proc = FakeProcessor()
    disp = _make(proc, debounce=0.15)
    # A Fix Match emits several state==5 entries in quick succession.
    for _ in range(5):
        disp.on_message(_timeline("7", 5))
    _wait(proc)
    # Let any stray timer fire too.
    import time
    time.sleep(0.1)
    assert proc.calls == [7]


def test_distinct_items_each_dispatch():
    proc = FakeProcessor()
    disp = _make(proc, debounce=0.02)
    disp.on_message(_timeline("1", 5))
    disp.on_message(_timeline("2", 5))
    # Wait for both to drain.
    import time
    deadline = time.time() + 1.0
    while len(proc.calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert sorted(proc.calls) == [1, 2]


def test_non_int_item_id_is_ignored():
    proc = FakeProcessor()
    disp = _make(proc)
    disp.on_message(_timeline("not-a-number", 5))
    assert not proc.event.wait(0.1)
    assert proc.calls == []


def test_missing_item_id_is_ignored():
    proc = FakeProcessor()
    disp = _make(proc)
    disp.on_message({
        "type": "timeline",
        "TimelineEntry": [
            {"identifier": "com.plexapp.plugins.library", "state": 5}
        ],
    })
    assert not proc.event.wait(0.1)
    assert proc.calls == []


def test_malformed_message_does_not_raise():
    proc = FakeProcessor()
    disp = _make(proc)
    # Should swallow and not kill the websocket thread.
    disp.on_message({"type": "timeline", "TimelineEntry": "not-a-list"})
    disp.on_message({})
    assert not proc.event.wait(0.1)
    assert proc.calls == []


def test_stop_cancels_pending_timers():
    proc = FakeProcessor()
    disp = _make(proc, debounce=5.0)  # long enough it won't fire on its own
    disp.on_message(_timeline("99", 5))
    disp.stop()
    assert not proc.event.wait(0.1)
    assert proc.calls == []

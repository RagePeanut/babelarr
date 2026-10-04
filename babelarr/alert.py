"""Plex WebSocket alert listener for new/re-matched media.

Unlike the ``library.new`` webhook (which only fires when an item is *first*
added, and requires Plex Pass), the alert stream reacts to **any** library
timeline activity — including a manual *Fix Match*, which re-downloads metadata
for an item that already exists. Plex never fires ``library.new`` for a
re-match, so this mode is the only real-time way to catch "the user corrected a
bad match".

How it works
------------
``PlexServer.startAlertListener(callback)`` opens a websocket to
``/:/websockets/notifications`` and hands each ``NotificationContainer`` to our
callback. For library processing Plex sends ``type == "timeline"`` messages
whose ``TimelineEntry`` items carry, for identifier
``com.plexapp.plugins.library``, a ``state`` integer:

    0 created · 1 progress · 2 matching · 3 downloading metadata
    4 processing metadata · 5 processed (done) · 9 deleted

We act only on ``state == 5`` (fully processed) so the item's metadata, posters
and streams are settled before Babelarr reads them. The ratingKey is the
entry's ``itemID``.

Caveats this mode accepts
-------------------------
* Plex has **no dedicated "match changed" event**. A ``2 -> 5`` cycle looks the
  same whether it came from a Fix Match, an automatic refresh, or scheduled
  maintenance. We therefore reprocess on every completed cycle and rely on
  Babelarr's per-field fingerprint state to make redundant runs no-ops.
* A single Fix Match emits several ``state == 5`` entries in a burst, so we
  **debounce per ratingKey** before dispatching.

Requires ``websocket-client`` at runtime (``pip install plexapi[alert]``).
"""

from __future__ import annotations

import logging
import threading

from .webhook import _safe_process

log = logging.getLogger("babelarr.alert")

# Identifier Plex uses for library (scanner/metadata) timeline entries.
_LIBRARY_IDENTIFIER = "com.plexapp.plugins.library"
# Timeline state meaning "the item finished processing" (metadata settled).
_STATE_PROCESSED = 5
# Coalesce the burst of state==5 entries a single match/refresh emits.
_DEBOUNCE_SECONDS = 5.0


class AlertDispatcher:
    """Turns Plex timeline notifications into debounced processor dispatches."""

    def __init__(self, processor, debounce_seconds: float = _DEBOUNCE_SECONDS):
        self._processor = processor
        self._debounce = debounce_seconds
        self._timers: dict[int, threading.Timer] = {}
        self._lock = threading.Lock()
        self._listener = None  # plexapi AlertListener (a threading.Thread)

    # -- websocket callbacks -------------------------------------------------

    def on_message(self, data: dict) -> None:
        """Handle one NotificationContainer from the alert websocket."""
        try:
            if data.get("type") != "timeline":
                return
            for entry in data.get("TimelineEntry", []) or []:
                if entry.get("identifier") != _LIBRARY_IDENTIFIER:
                    continue
                if entry.get("state") != _STATE_PROCESSED:
                    continue
                item_id = entry.get("itemID")
                if item_id is None:
                    continue
                try:
                    rating_key = int(item_id)
                except (TypeError, ValueError):
                    log.debug("Timeline entry with non-int itemID %r", item_id)
                    continue
                self._schedule(rating_key)
        except Exception:  # pragma: no cover - defensive, never kill the socket
            log.exception("Error handling alert message")

    def on_error(self, error) -> None:
        log.warning("Alert listener error: %s", error)

    # -- debounce ------------------------------------------------------------

    def _schedule(self, rating_key: int) -> None:
        """(Re)start the debounce timer for a ratingKey."""
        with self._lock:
            existing = self._timers.get(rating_key)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(
                self._debounce, self._fire, args=(rating_key,)
            )
            timer.daemon = True
            self._timers[rating_key] = timer
            timer.start()
        log.debug(
            "Timeline processed -> ratingKey=%s (debouncing %.1fs)",
            rating_key, self._debounce,
        )

    def _fire(self, rating_key: int) -> None:
        with self._lock:
            self._timers.pop(rating_key, None)
        log.info("Alert timeline -> processing ratingKey=%s", rating_key)
        _safe_process(self._processor, rating_key)

    # -- lifecycle -----------------------------------------------------------

    def start(self, server) -> "AlertDispatcher":
        self._listener = server.startAlertListener(
            callback=self.on_message, callbackError=self.on_error
        )
        log.info("Alert listener started (Plex websocket)")
        return self

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # pragma: no cover - best-effort shutdown
                log.debug("AlertListener stop raised", exc_info=True)
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()


def serve(processor, server) -> AlertDispatcher:
    """Start the alert listener; returns a handle with a ``stop()`` method."""
    return AlertDispatcher(processor).start(server)

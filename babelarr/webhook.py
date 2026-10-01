"""Tiny stdlib HTTP server for Plex webhooks (library.new).

Plex sends a multipart/form-data POST with a ``payload`` field containing JSON.
We only act on ``library.new`` events, dispatching the item's ratingKey to the
processor. No external web framework needed.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("babelarr.webhook")


def _extract_payload(body: bytes, content_type: str) -> dict:
    # Plex uses multipart/form-data with a "payload" part. Parse leniently.
    if b'name="payload"' in body:
        marker = b'name="payload"'
        start = body.find(marker)
        # payload JSON begins after the blank line following the header.
        json_start = body.find(b"{", start)
        json_end = body.rfind(b"}")
        if json_start != -1 and json_end != -1:
            return json.loads(body[json_start : json_end + 1])
    # Fallback: raw JSON body.
    return json.loads(body.decode("utf-8"))


def make_handler(processor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default logging
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            self.send_response(200)
            self.end_headers()
            try:
                payload = _extract_payload(body, self.headers.get("Content-Type", ""))
            except Exception:
                log.warning("Could not parse webhook payload")
                return

            event = payload.get("event")
            if event != "library.new":
                return
            meta = payload.get("Metadata", {})
            rating_key = meta.get("ratingKey")
            if rating_key is None:
                return
            log.info("Webhook library.new -> ratingKey=%s (%s)",
                     rating_key, meta.get("title"))
            threading.Thread(
                target=_safe_process, args=(processor, rating_key), daemon=True
            ).start()

    return Handler


def _safe_process(processor, rating_key):
    try:
        processor.process_rating_key(rating_key)
    except Exception:
        log.exception("Webhook processing failed for ratingKey=%s", rating_key)


def serve(processor, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(processor))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Webhook listener started on port %s", port)
    return server

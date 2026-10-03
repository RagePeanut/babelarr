"""Tiny stdlib HTTP server for Plex webhooks (library.new).

Plex sends a ``multipart/form-data`` POST whose ``payload`` field holds the
event JSON — and, for events like ``library.new``, a second ``thumb`` field
containing a **binary image**. We extract only the ``payload`` field (parsing
the multipart body properly using the boundary from the Content-Type header),
act on ``library.new`` events, and dispatch the item's ratingKey. No external
web framework needed.
"""

from __future__ import annotations

import json
import logging
import threading
from email.parser import BytesParser
from email.policy import default as _email_default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("babelarr.webhook")


class PayloadError(Exception):
    """Raised when the webhook body can't be turned into event JSON."""


def _parse_multipart_payload(body: bytes, content_type: str) -> bytes:
    """Return the raw bytes of the ``payload`` form field from a multipart body.

    Uses the boundary declared in the Content-Type header and the stdlib email
    parser, so a trailing binary ``thumb`` image part is handled correctly
    (unlike a naive first-'{' / last-'}' slice, which can slice into the image).
    """
    # Reconstruct a minimal MIME document the email parser understands.
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n"
    msg = BytesParser(policy=_email_default_policy).parsebytes(
        header.encode("utf-8") + body
    )
    if not msg.is_multipart():
        raise PayloadError("multipart body expected but not found")
    for part in msg.iter_parts():
        disp = part.get("Content-Disposition", "")
        if 'name="payload"' in disp or "name=payload" in disp:
            return part.get_payload(decode=True) or b""
    raise PayloadError("no 'payload' part in multipart body")


def _extract_payload(body: bytes, content_type: str) -> dict:
    """Parse a Plex webhook body into the event dict.

    Handles the normal ``multipart/form-data`` form (payload + optional image),
    an ``application/json`` body, and a bare ``payload=`` URL-encoded form, in
    that order.
    """
    ctype = (content_type or "").lower()

    if "multipart/form-data" in ctype and "boundary=" in ctype:
        raw = _parse_multipart_payload(body, content_type)
        return json.loads(raw.decode("utf-8"))

    # Some setups / proxies send the JSON directly.
    stripped = body.strip()
    if stripped.startswith(b"{"):
        return json.loads(stripped.decode("utf-8"))

    # URL-encoded "payload=..." fallback (value may be percent-encoded).
    if body.startswith(b"payload="):
        from urllib.parse import unquote_plus

        raw = unquote_plus(body[len(b"payload="):].decode("utf-8", "replace"))
        raw = raw.strip()
        if raw.startswith("{"):
            return json.loads(raw)

    raise PayloadError(
        f"unrecognized webhook body (content-type={content_type!r}, "
        f"{len(body)} bytes)"
    )


def make_handler(processor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default logging
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            # Always acknowledge quickly so Plex doesn't retry/err.
            self.send_response(200)
            self.end_headers()

            content_type = self.headers.get("Content-Type", "")
            try:
                payload = _extract_payload(body, content_type)
            except PayloadError as exc:
                log.warning("Could not parse webhook payload: %s", exc)
                return
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                log.warning(
                    "Could not parse webhook payload JSON (content-type=%r): %s",
                    content_type, exc,
                )
                return
            except Exception:  # pragma: no cover - defensive
                log.exception("Unexpected error parsing webhook payload")
                return

            event = payload.get("event")
            if event != "library.new":
                log.debug("Ignoring webhook event %r", event)
                return
            meta = payload.get("Metadata", {}) or {}
            rating_key = meta.get("ratingKey")
            if rating_key is None:
                log.debug("library.new webhook had no ratingKey; ignoring")
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

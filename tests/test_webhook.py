"""Tests for Plex webhook payload parsing.

The key regression: Plex sends library.new as multipart/form-data with the JSON
``payload`` field AND a trailing binary ``thumb`` image. A naive first-'{' /
last-'}' slice can cut into the image bytes and fail. These tests build bodies
that reproduce that and assert correct extraction.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from babelarr.webhook import _extract_payload, PayloadError  # noqa: E402

BOUNDARY = "----boundary123"
CT_MULTIPART = f'multipart/form-data; boundary={BOUNDARY}'


def _multipart(payload_json: str, image_bytes: bytes = b"") -> bytes:
    """Build a multipart body like Plex sends: a payload part + optional image."""
    parts = []
    parts.append(
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="payload"\r\n\r\n'
        f"{payload_json}\r\n"
    )
    body = "".join(parts).encode("utf-8")
    if image_bytes:
        body += (
            f"--{BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="thumb"; filename="thumb.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode("utf-8") + image_bytes + b"\r\n"
    body += f"--{BOUNDARY}--\r\n".encode("utf-8")
    return body


PAYLOAD = '{"event":"library.new","Metadata":{"ratingKey":"42","title":"X"}}'


def test_multipart_payload_only():
    body = _multipart(PAYLOAD)
    data = _extract_payload(body, CT_MULTIPART)
    assert data["event"] == "library.new"
    assert data["Metadata"]["ratingKey"] == "42"


def test_multipart_with_trailing_image_containing_braces():
    # Binary image bytes that include '}' — this is what broke the old parser.
    image = bytes([0xFF, 0xD8, 0x7D, 0x7D, 0x00, 0x7D, 0xFF, 0xD9])  # has 0x7D = '}'
    body = _multipart(PAYLOAD, image_bytes=image)
    data = _extract_payload(body, CT_MULTIPART)
    assert data["event"] == "library.new"
    assert data["Metadata"]["ratingKey"] == "42"


def test_raw_json_body():
    data = _extract_payload(PAYLOAD.encode("utf-8"), "application/json")
    assert data["event"] == "library.new"


def test_urlencoded_payload():
    from urllib.parse import quote_plus
    body = ("payload=" + quote_plus(PAYLOAD)).encode("utf-8")
    data = _extract_payload(body, "application/x-www-form-urlencoded")
    assert data["event"] == "library.new"


def test_multipart_missing_payload_part_raises():
    body = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="thumb"\r\n\r\n'
        "notjson\r\n"
        f"--{BOUNDARY}--\r\n"
    ).encode("utf-8")
    with pytest.raises(PayloadError):
        _extract_payload(body, CT_MULTIPART)


def test_unrecognized_body_raises():
    with pytest.raises(PayloadError):
        _extract_payload(b"garbage not json", "text/plain")


def test_multipart_payload_with_nested_braces():
    # JSON with nested objects — ensure we don't truncate at the first '}'.
    payload = (
        '{"event":"library.new","Metadata":'
        '{"ratingKey":"7","Guid":[{"id":"tmdb://1"}]}}'
    )
    body = _multipart(payload, image_bytes=b"\xff\xd8}}\xff\xd9")
    data = _extract_payload(body, CT_MULTIPART)
    assert data["Metadata"]["ratingKey"] == "7"
    assert data["Metadata"]["Guid"][0]["id"] == "tmdb://1"

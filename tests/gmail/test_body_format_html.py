"""Tests for body_format="html" / "raw" on the Gmail read tools.

Each fixture asserts that body_format="text" output is unchanged from the
existing behaviour and that body_format="html" returns the text/html part
(or the documented text/plain fallback) with the HTML references block.
"""

import base64
from unittest.mock import Mock

import pytest

import gmail.gmail_helpers as gmail_helpers
import gmail.gmail_tools as gmail_tools
from gmail.gmail_tools import (
    HTML_NO_HTML_PART_NOTICE,
    _extract_html_body,
    _extract_message_bodies,
    _format_body_content,
    _format_thread_content,
    get_gmail_message_content,
    get_gmail_messages_content_batch,
    get_gmail_thread_content,
)

from tests.gmail.test_body_format import (
    _build_service,
    _encode,
    _headers,
    _metadata_response,
    _unwrap,
)

PLAIN_TEXT = "Hello from plain text"
HTML_BODY = (
    '<div dir="ltr">Hello from <b>HTML</b>'
    '<img src="https://img.example.com/BIR-Logo.png" alt="BIR Logo">'
    '<img src="https://img.example.com/frame-artwork.png" alt="frame-artwork">'
    "</div>"
)
CID_HTML_BODY = (
    '<div>Signature <img src="cid:ii_logo123" alt="logo">'
    '<img src="https://img.example.com/remote.png"></div>'
)

EXPECTED_HEADER_LINES = [
    "Subject: Example subject",
    "From: sender@example.com",
    "Date: Fri, 28 Mar 2026 10:00:00 -0400",
    "Message-ID: <message@example.com>",
    "To: recipient@example.com",
    "Cc: cc@example.com",
]


def _text_part(text, **extra):
    return {
        "partId": extra.pop("partId", "0"),
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": 'text/plain; charset="UTF-8"'}],
        "body": {"size": len(text), "data": _encode(text)},
        **extra,
    }


def _html_part(html, **extra):
    return {
        "partId": extra.pop("partId", "1"),
        "mimeType": "text/html",
        "headers": [{"name": "Content-Type", "value": 'text/html; charset="UTF-8"'}],
        "body": {"size": len(html), "data": _encode(html)},
        **extra,
    }


# (a) plain text only
def _fixture_plain_text_only():
    return {
        "mimeType": "text/plain",
        "headers": _headers(),
        "body": {"size": len(PLAIN_TEXT), "data": _encode(PLAIN_TEXT)},
    }


# (b) multipart/alternative with text and html
def _fixture_alternative():
    return {
        "mimeType": "multipart/alternative",
        "headers": _headers(),
        "body": {"size": 0},
        "parts": [_text_part(PLAIN_TEXT), _html_part(HTML_BODY)],
    }


# (c) multipart/mixed containing multipart/related with a cid image
def _fixture_mixed_related_cid():
    return {
        "mimeType": "multipart/mixed",
        "headers": _headers(),
        "body": {"size": 0},
        "parts": [
            {
                "partId": "0",
                "mimeType": "multipart/related",
                "body": {"size": 0},
                "parts": [
                    {
                        "partId": "0.0",
                        "mimeType": "multipart/alternative",
                        "body": {"size": 0},
                        "parts": [
                            _text_part(PLAIN_TEXT, partId="0.0.0"),
                            _html_part(CID_HTML_BODY, partId="0.0.1"),
                        ],
                    },
                    {
                        "partId": "0.1",
                        "mimeType": "image/png",
                        "filename": "logo.png",
                        "headers": [
                            {"name": "Content-Type", "value": "image/png"},
                            {"name": "Content-ID", "value": "<ii_logo123>"},
                            {"name": "X-Attachment-Id", "value": "ii_logo123"},
                        ],
                        "body": {"size": 1234, "attachmentId": "ATT-LOGO"},
                    },
                ],
            },
            {
                "partId": "1",
                "mimeType": "application/pdf",
                "filename": "report.pdf",
                "body": {"size": 5678, "attachmentId": "ATT-PDF"},
            },
        ],
    }


# (d) html stored under attachmentId (no body.data)
def _fixture_html_under_attachment_id():
    html_part = _html_part(HTML_BODY)
    html_part["body"] = {"size": len(HTML_BODY), "attachmentId": "ATT-HTML-BODY"}
    return {
        "mimeType": "multipart/alternative",
        "headers": _headers(),
        "body": {"size": 0},
        "parts": [_text_part(PLAIN_TEXT), html_part],
    }


def _service_for(payload, message_id="msg-1", attachment_data=None):
    service = _build_service(
        message_responses={
            (message_id, "metadata"): _metadata_response(message_id),
            (message_id, "full"): {"id": message_id, "payload": payload},
        }
    )
    attachments_get = service.users().messages().attachments().get
    attachment_calls = []

    def _attachment_get(**kwargs):
        attachment_calls.append(kwargs)
        request = Mock()
        request.execute.return_value = {"data": attachment_data or ""}
        return request

    attachments_get.side_effect = _attachment_get
    return service, attachment_calls


def _expected_text_output(body):
    return "\n".join(EXPECTED_HEADER_LINES + [f"\n--- BODY ---\n{body}"])


async def _call_message(service, body_format=None, message_id="msg-1"):
    kwargs = {}
    if body_format is not None:
        kwargs["body_format"] = body_format
    return await _unwrap(get_gmail_message_content)(
        service=service,
        message_id=message_id,
        user_google_email="user@example.com",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# (a) plain text only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_text_only_text_mode_unchanged():
    service, attachment_calls = _service_for(_fixture_plain_text_only())

    default_result = await _call_message(service)
    explicit_result = await _call_message(service, body_format="text")

    assert default_result == _expected_text_output(PLAIN_TEXT)
    assert explicit_result == default_result
    assert "--- HTML REFERENCES ---" not in default_result
    assert attachment_calls == []


@pytest.mark.asyncio
async def test_plain_text_only_html_mode_falls_back_with_notice():
    service, _ = _service_for(_fixture_plain_text_only())

    result = await _call_message(service, body_format="html")

    assert "--- HTML REFERENCES ---" in result
    assert "Image URLs: none" in result
    assert "Inline cid references: none" in result
    assert f"--- BODY ---\n{HTML_NO_HTML_PART_NOTICE}\n{PLAIN_TEXT}" in result


# ---------------------------------------------------------------------------
# (b) multipart/alternative with text and html
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alternative_text_mode_unchanged():
    service, attachment_calls = _service_for(_fixture_alternative())

    result = await _call_message(service, body_format="text")

    assert result == _expected_text_output(PLAIN_TEXT)
    assert "<img" not in result
    assert attachment_calls == []


@pytest.mark.asyncio
async def test_alternative_html_mode_returns_html_with_image_urls():
    service, attachment_calls = _service_for(_fixture_alternative())

    result = await _call_message(service, body_format="html")

    assert f"--- BODY ---\n{HTML_BODY}" in result
    assert HTML_NO_HTML_PART_NOTICE not in result
    assert "Image URLs (2):" in result
    assert "1. https://img.example.com/BIR-Logo.png" in result
    assert "2. https://img.example.com/frame-artwork.png" in result
    assert "Inline cid references: none" in result
    # The references block comes before the body
    assert result.index("--- HTML REFERENCES ---") < result.index("--- BODY ---")
    assert attachment_calls == []


def test_alternative_helper_text_path_unchanged():
    bodies = _extract_message_bodies(_fixture_alternative())
    assert bodies == {"text": PLAIN_TEXT, "html": HTML_BODY}
    assert _format_body_content(bodies["text"], bodies["html"]) == PLAIN_TEXT


def test_html_part_sibling_of_text_is_preferred():
    outer_html = "<p>outer html (not the alternative sibling)</p>"
    payload = {
        "mimeType": "multipart/mixed",
        "headers": _headers(),
        "parts": [
            _html_part(outer_html, partId="0"),
            {
                "partId": "1",
                "mimeType": "multipart/alternative",
                "parts": [
                    _text_part(PLAIN_TEXT, partId="1.0"),
                    _html_part(HTML_BODY, partId="1.1"),
                ],
            },
        ],
    }
    assert _extract_html_body(payload)["html"] == HTML_BODY


# ---------------------------------------------------------------------------
# (c) multipart/mixed > multipart/related with a cid image
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_related_text_mode_unchanged():
    service, attachment_calls = _service_for(_fixture_mixed_related_cid())

    result = await _call_message(service, body_format="text")

    expected = _expected_text_output(PLAIN_TEXT) + (
        "\n\n--- ATTACHMENTS ---\n"
        "1. logo.png (image/png, 1.2 KB)\n"
        "   Attachment ID: ATT-LOGO\n"
        "   Use get_gmail_attachment_content(message_id='msg-1', attachment_id='ATT-LOGO') to download\n"
        "2. report.pdf (application/pdf, 5.5 KB)\n"
        "   Attachment ID: ATT-PDF\n"
        "   Use get_gmail_attachment_content(message_id='msg-1', attachment_id='ATT-PDF') to download"
    )
    assert result == expected
    assert attachment_calls == []


@pytest.mark.asyncio
async def test_mixed_related_html_mode_maps_cid_to_attachment():
    service, attachment_calls = _service_for(_fixture_mixed_related_cid())

    result = await _call_message(service, body_format="html")

    assert f"--- BODY ---\n{CID_HTML_BODY}" in result
    assert "Image URLs (2):" in result
    assert "1. cid:ii_logo123" in result
    assert "2. https://img.example.com/remote.png" in result
    assert "Inline cid references (1):" in result
    assert "1. cid:ii_logo123 -> logo.png (image/png), attachmentId: ATT-LOGO" in result
    assert (
        "get_gmail_attachment_content(message_id='msg-1', attachment_id='ATT-LOGO')"
        in result
    )
    # The existing attachments block is still present and unchanged
    assert "--- ATTACHMENTS ---" in result
    assert "2. report.pdf (application/pdf, 5.5 KB)" in result
    # Inline body had data, so nothing was fetched via attachments.get
    assert attachment_calls == []


def test_extract_html_body_unmatched_cid_is_reported():
    payload = _fixture_alternative()
    payload["parts"][1] = _html_part('<img src="cid:missing@x">')
    result = _extract_html_body(payload)
    assert result["cid_refs"] == [{"cid": "missing@x"}]
    rendered = "\n".join(gmail_tools._render_html_references(result, "m"))
    assert "cid:missing@x -> (no matching attachment part)" in rendered


# ---------------------------------------------------------------------------
# (d) html stored under attachmentId
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_html_under_attachment_id_text_mode_unchanged():
    service, attachment_calls = _service_for(
        _fixture_html_under_attachment_id(), attachment_data=_encode(HTML_BODY)
    )

    result = await _call_message(service, body_format="text")

    assert result == _expected_text_output(PLAIN_TEXT)
    # Text mode never fetches separately stored parts
    assert attachment_calls == []


@pytest.mark.asyncio
async def test_html_under_attachment_id_html_mode_fetches_part():
    service, attachment_calls = _service_for(
        _fixture_html_under_attachment_id(), attachment_data=_encode(HTML_BODY)
    )

    result = await _call_message(service, body_format="html")

    assert f"--- BODY ---\n{HTML_BODY}" in result
    assert "1. https://img.example.com/BIR-Logo.png" in result
    assert attachment_calls == [
        {"userId": "me", "messageId": "msg-1", "id": "ATT-HTML-BODY"}
    ]


@pytest.mark.asyncio
async def test_html_under_attachment_id_fetch_failure_falls_back_to_text():
    service, _ = _service_for(_fixture_html_under_attachment_id())
    service.users().messages().attachments().get.side_effect = RuntimeError("boom")

    result = await _call_message(service, body_format="html")

    assert f"--- BODY ---\n{HTML_NO_HTML_PART_NOTICE}\n{PLAIN_TEXT}" in result


# ---------------------------------------------------------------------------
# (e) raw
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raw_mode_returns_decoded_rfc2822_source():
    raw_source = (
        "From: sender@example.com\r\nTo: recipient@example.com\r\n"
        "Subject: Example subject\r\nContent-Type: multipart/alternative; "
        'boundary="b"\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\n'
        f"{PLAIN_TEXT}\r\n--b\r\nContent-Type: text/html\r\n\r\n{HTML_BODY}\r\n--b--\r\n"
    )
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "full"): {"id": "msg-1", "payload": _fixture_alternative()},
            ("msg-1", "raw"): {"raw": _encode(raw_source)},
        }
    )

    text_result = await _call_message(service, body_format="text")
    raw_result = await _call_message(service, body_format="raw")

    assert text_result == _expected_text_output(PLAIN_TEXT)
    assert raw_result == "\n".join(
        EXPECTED_HEADER_LINES + [f"\n--- RAW MIME ---\n{raw_source}"]
    )
    assert "[Content truncated...]" not in raw_result


def test_raw_cap_is_one_megabyte():
    assert gmail_helpers.RAW_BODY_TRUNCATE_LIMIT == 1024 * 1024
    assert gmail_tools.RAW_BODY_TRUNCATE_LIMIT == 1024 * 1024


def test_raw_truncation_reports_full_size(monkeypatch):
    monkeypatch.setattr(gmail_tools, "RAW_BODY_TRUNCATE_LIMIT", 10)
    raw_bytes = b"x" * 25
    encoded = base64.urlsafe_b64encode(raw_bytes).decode()

    result = gmail_tools._decode_raw_mime_content(encoded)

    assert result.startswith("x" * 10 + "\n\n[Content truncated...]")
    assert "Raw message is 25 bytes; showing the first 10 bytes." in result


def test_raw_under_cap_is_not_truncated():
    raw_bytes = b"y" * 100
    encoded = base64.urlsafe_b64encode(raw_bytes).decode()
    assert gmail_tools._decode_raw_mime_content(encoded) == "y" * 100


# ---------------------------------------------------------------------------
# Batch and thread tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_tool_html_mode_and_text_mode():
    service, attachment_calls = _service_for(_fixture_alternative())

    text_result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1"],
        user_google_email="user@example.com",
    )
    html_result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1"],
        user_google_email="user@example.com",
        body_format="html",
    )

    assert text_result == (
        "Retrieved 1 messages:\n\n"
        "Message ID: msg-1\n" + "\n".join(EXPECTED_HEADER_LINES) + "\n"
        "Web Link: https://mail.google.com/mail/u/0/#all/msg-1\n"
        f"\n--- BODY ---\n{PLAIN_TEXT}\n"
    )
    assert "--- HTML REFERENCES ---" in html_result
    assert "1. https://img.example.com/BIR-Logo.png" in html_result
    assert f"--- BODY ---\n{HTML_BODY}\n" in html_result
    assert html_result.index("--- HTML REFERENCES ---") < html_result.index(
        "--- BODY ---"
    )
    assert attachment_calls == []


@pytest.mark.asyncio
async def test_thread_tool_html_mode_and_text_mode():
    thread = {
        "id": "thread-1",
        "messages": [
            {"id": "msg-1", "payload": _fixture_alternative()},
            {"id": "msg-2", "payload": _fixture_plain_text_only()},
        ],
    }
    service = _build_service(thread_responses={("thread-1", "full"): thread})

    text_result = await _unwrap(get_gmail_thread_content)(
        service=service, thread_id="thread-1", user_google_email="user@example.com"
    )
    html_result = await _unwrap(get_gmail_thread_content)(
        service=service,
        thread_id="thread-1",
        user_google_email="user@example.com",
        body_format="html",
    )

    assert text_result == _format_thread_content(thread, "thread-1")
    assert text_result.count(PLAIN_TEXT) == 2
    assert "<img" not in text_result
    assert "--- HTML REFERENCES ---" not in text_result

    assert html_result.count("--- HTML REFERENCES ---") == 2
    assert HTML_BODY in html_result
    assert "1. https://img.example.com/BIR-Logo.png" in html_result
    assert f"{HTML_NO_HTML_PART_NOTICE}\n{PLAIN_TEXT}" in html_result

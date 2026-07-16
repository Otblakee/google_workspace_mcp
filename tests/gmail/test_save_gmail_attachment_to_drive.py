"""
Tests for ``save_gmail_attachment_to_drive``, the server-side Gmail -> Drive
attachment transfer. The attachment content must go straight from the Gmail
API to the Drive API without appearing in the tool response, so it works for
attachments of any size and in stateless deployments.
"""

import base64
from typing import Any, Callable
from unittest.mock import Mock

import pytest

from core.server import server
from core.tool_registry import get_tool_components
from gmail.gmail_tools import save_gmail_attachment_to_drive


def _unwrap(tool: Any) -> Callable[..., Any]:
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _build_mock_gmail_service(
    payload: bytes,
    *,
    filename: str = "attachment.bin",
    mime_type: str = "application/octet-stream",
    attachment_id: str = "att-123",
) -> Mock:
    """Build a Mock Gmail service returning ``payload`` as an attachment."""
    urlsafe_b64 = base64.urlsafe_b64encode(payload).decode("ascii")

    mock_service = Mock()
    mock_service.users().messages().attachments().get().execute.return_value = {
        "size": len(payload),
        "data": urlsafe_b64,
    }
    mock_service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": filename,
                    "mimeType": mime_type,
                    "body": {"attachmentId": attachment_id, "size": len(payload)},
                }
            ],
        },
    }
    return mock_service


def _build_mock_drive_service(
    *,
    file_id: str = "drive-file-1",
    name: str = "attachment.bin",
    link: str = "https://drive.google.com/file/d/drive-file-1/view",
) -> Mock:
    mock_service = Mock()
    mock_service.files().create().execute.return_value = {
        "id": file_id,
        "name": name,
        "webViewLink": link,
    }
    # Reset so call_args in assertions reflect only the call under test.
    mock_service.files().create.reset_mock()
    return mock_service


def test_schema_exposes_folder_id_and_file_name():
    """Published MCP schema should expose folder_id (default 'root') and file_name."""
    components = get_tool_components(server)
    schema = components[save_gmail_attachment_to_drive.__name__].parameters[
        "properties"
    ]

    assert "folder_id" in schema
    assert schema["folder_id"]["default"] == "root"
    assert "file_name" in schema


@pytest.mark.asyncio
async def test_uploads_attachment_bytes_to_drive_folder():
    """The exact attachment bytes must be uploaded to the requested folder."""
    payload = b"%PDF-1.7 " + bytes(range(256)) * 4
    gmail_service = _build_mock_gmail_service(
        payload, filename="invoice.pdf", mime_type="application/pdf"
    )
    drive_service = _build_mock_drive_service(name="invoice.pdf")

    result = await _unwrap(save_gmail_attachment_to_drive)(
        gmail_service=gmail_service,
        drive_service=drive_service,
        user_google_email="user@example.com",
        message_id="msg-1",
        attachment_id="att-123",
        folder_id="folder-01-raw",
    )

    create_kwargs = drive_service.files().create.call_args.kwargs
    assert create_kwargs["body"] == {
        "name": "invoice.pdf",
        "parents": ["folder-01-raw"],
    }
    assert create_kwargs["supportsAllDrives"] is True

    media = create_kwargs["media_body"]
    assert media.mimetype() == "application/pdf"
    assert media.getbytes(0, media.size()) == payload

    assert "Attachment saved to Google Drive!" in result
    assert "drive-file-1" in result
    assert "https://drive.google.com/file/d/drive-file-1/view" in result
    # The attachment content must never be relayed in the response.
    assert base64.urlsafe_b64encode(payload).decode("ascii") not in result
    assert base64.b64encode(payload).decode("ascii") not in result


@pytest.mark.asyncio
async def test_defaults_to_root_folder_and_resolved_filename():
    payload = b"hello attachment"
    gmail_service = _build_mock_gmail_service(
        payload, filename="notes.txt", mime_type="text/plain"
    )
    drive_service = _build_mock_drive_service(name="notes.txt")

    await _unwrap(save_gmail_attachment_to_drive)(
        gmail_service=gmail_service,
        drive_service=drive_service,
        user_google_email="user@example.com",
        message_id="msg-1",
        attachment_id="att-123",
    )

    create_kwargs = drive_service.files().create.call_args.kwargs
    assert create_kwargs["body"] == {"name": "notes.txt", "parents": ["root"]}


@pytest.mark.asyncio
async def test_file_name_override_wins_over_resolved_name():
    payload = b"renamed content"
    gmail_service = _build_mock_gmail_service(
        payload, filename="original.csv", mime_type="text/csv"
    )
    drive_service = _build_mock_drive_service(name="renamed.csv")

    await _unwrap(save_gmail_attachment_to_drive)(
        gmail_service=gmail_service,
        drive_service=drive_service,
        user_google_email="user@example.com",
        message_id="msg-1",
        attachment_id="att-123",
        file_name="renamed.csv",
    )

    create_kwargs = drive_service.files().create.call_args.kwargs
    assert create_kwargs["body"]["name"] == "renamed.csv"


@pytest.mark.asyncio
async def test_falls_back_to_generated_name_when_metadata_unresolvable():
    """If filename resolution fails, a deterministic fallback name is used."""
    payload = b"mystery bytes"
    gmail_service = _build_mock_gmail_service(payload)
    # Make the metadata re-fetch blow up so resolution returns (None, None).
    gmail_service.users().messages().get().execute.side_effect = RuntimeError("boom")
    drive_service = _build_mock_drive_service()

    await _unwrap(save_gmail_attachment_to_drive)(
        gmail_service=gmail_service,
        drive_service=drive_service,
        user_google_email="user@example.com",
        message_id="msg-1",
        attachment_id="att-1234567890abcdef-long-id",
    )

    create_kwargs = drive_service.files().create.call_args.kwargs
    assert create_kwargs["body"]["name"] == "gmail-attachment-att-1234567890ab"
    assert create_kwargs["media_body"].mimetype() == "application/octet-stream"


@pytest.mark.asyncio
async def test_attachment_download_failure_returns_guidance():
    """A failed Gmail download should explain that attachment IDs are ephemeral."""
    gmail_service = Mock()
    gmail_service.users().messages().attachments().get().execute.side_effect = (
        RuntimeError("attachment gone")
    )
    drive_service = _build_mock_drive_service()

    result = await _unwrap(save_gmail_attachment_to_drive)(
        gmail_service=gmail_service,
        drive_service=drive_service,
        user_google_email="user@example.com",
        message_id="msg-1",
        attachment_id="att-stale",
    )

    assert "Error: Failed to download attachment" in result
    assert "attachment ID may have changed" in result
    drive_service.files().create.assert_not_called()

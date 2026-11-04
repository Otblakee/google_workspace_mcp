# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.21.0] - 2026-11-04

### Added

- Gmail read tools (`get_gmail_message_content`, `get_gmail_messages_content_batch`,
  `get_gmail_thread_content`, `get_gmail_threads_content_batch`): `body_format="html"`
  now walks the full MIME tree (multipart/alternative, multipart/mixed,
  multipart/related, nested parts), prefers the text/html part that sits next to the
  text/plain part, and fetches body parts stored under `attachmentId` via
  `messages.attachments.get`.
- `body_format="html"` output is preceded by an `--- HTML REFERENCES ---` block listing
  every `<img src>` URL and every `cid:` reference with the matching attachment part's
  filename and `attachmentId`, so inline images can be downloaded with
  `get_gmail_attachment_content`.
- When no text/html part exists, `body_format="html"` returns the text/plain part after
  the line `(no text/html part; showing text/plain)`.
- Unit tests covering plain text only, multipart/alternative, multipart/mixed with a
  multipart/related cid image, html stored under `attachmentId`, and raw output.

### Changed

- `body_format="raw"` is now capped at 1 MB (was 20,000 characters). Larger messages are
  truncated and the full size is reported in the truncation notice.
- Tool docstrings and schema descriptions for `body_format` describe the html references
  block, the text/plain fallback notice and the raw size cap.

### Unchanged

- `body_format="text"` (the default) output is byte-identical to 1.20.1. Text mode never
  fetches separately stored body parts. The batch tool's `format` parameter
  (`full` | `metadata`) is unchanged.

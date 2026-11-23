# Ideas

## Tunnel startup automation — implemented and live-checked

`tunnelctl.py` provides Keychain key storage, manual start/stop, readiness,
duplicate prevention, explicit write mode and a one-click applet; it passed a
live run with ChatGPT (see TESTING.md). Still open: optional login-time startup
(LaunchAgent) and whether to launch Anki.

## Japanese TTS MP3 attachments — proposed

Choose text, voice/provider, privacy and cost expectations. Preview pronunciation
before approving a card. Define media naming, caching, duplicate handling,
cleanup after cancellation and backups. Current validation rejects sound
references; add media support deliberately rather than removing safeguards.

## Claude Code support — proposed

Added 2026-11-17. Document and validate using this server from Claude Code,
in addition to Codex and the ChatGPT plugin. This is a future integration task;
Claude Code compatibility is not yet verified.

Planned work:
- Check current official Claude Code MCP setup, configuration scope, tool
  approval and timeout behavior before writing installation instructions.
- Aim to reuse the local stdio server and ignored deck/model configuration,
  without introducing a dependency on the OpenAI tunnel for this client.
- Provide generic registration/removal instructions with correct path quoting,
  read-only setup first and an explicit transition to confirmed additions.
- Test discovery, deck listing, previews, cancellation, approved addition,
  duplicate rejection and expired previews from a real Claude Code session.
- Preserve the configured-deck restriction and native Mac approval. Client
  approval must not replace or bypass the server's confirmation dialog.
- Record tested client/runtime versions and limitations before claiming support.

Keep account/subscription details and installation-specific test notes local.

## Portability and migration — partly prepared

Local JSON settings and runtime-generated paths remove personal constants from
the shared source. The generic tunnel has passed live checks. Consolidating
existing client registrations and retiring an older installation are separate
optional steps; use MIGRATION.md.

## Further release validation — proposed

MIT licensing, a fresh environment install, 14 unit tests, Codex read-only calls
and confirmed tunnel additions are complete. Broaden coverage to additional
Python/macOS versions, a clean machine/account, and Codex-specific write calls.
Repeat source/history review before future publications.

Record future proposals here; record delivered updates in CHANGELOG.md.

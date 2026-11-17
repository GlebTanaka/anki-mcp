# Ideas

## Tunnel startup automation — proposed

Explore a one-click launcher and optional login-time startup. Choose secure
persistent key storage first, for example macOS Keychain. Provide start/stop,
health/readiness, duplicate-process prevention and explicit write mode. Retain
local AnkiConnect and human approval. Decide separately whether to launch Anki.

## Japanese TTS MP3 attachments — proposed

Choose text, voice/provider, privacy and cost expectations. Preview pronunciation
before approving a card. Define media naming, caching, duplicate handling,
cleanup after cancellation and backups. Current validation rejects sound
references; add media support deliberately rather than removing safeguards.

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

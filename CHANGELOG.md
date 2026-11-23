# Changelog

## Unreleased

- Addressed review findings. Duplicate prevention now uses one per-user,
  per-tunnel lock shared by all checkouts and both launchers and held by the
  running client itself. The process scan reads exact arguments, parses the
  configuration and fails closed when processes cannot be inspected or
  identified.
- HTML validation rejects processing instructions and CDATA. Cards with
  invisible or direction-changing characters are rejected. The approval
  dialog marks every line of card text and shows the deck in its title.
- Previews no longer wait while an approval dialog is open; additions remain
  one at a time, single-use and rechecked. 34 tests pass (7 new).

- Added `tunnelctl.py`: background Start/Stop, runtime key in the macOS
  Keychain, `/readyz` readiness and status, a lock plus process scan that
  prevents two clients for one tunnel ID, and an optional one-click applet.
  Additions mode requires the desktop session; the Mac approval dialog is
  unchanged. 13 new offline tests use a fake tunnel client; a live run with
  the real tunnel client and ChatGPT passed, including approved additions.
- The tunnel profile now launches the MCP server via `env -u
  CONTROL_PLANE_API_KEY` so the runtime key is not inherited by the server or
  its approval dialog. `start_tunnel.py` refuses to start beside another client.

- Added a proposed Claude Code integration to the roadmap, with setup, safety
  and live-test criteria; compatibility is not yet verified.

- Completed a fresh project-environment installation and 14 passing unit tests.
- Verified local preview, cancellation, approved addition and duplicate rejection.
- Verified read-only tools from a fresh Codex chat and live plugin calls through
  the generic tunnel, including an approved addition and duplicate rejection.
- Prepared public sharing with generic test documentation and a source/history
  review; installation-specific settings and test identifiers remain local.

- Added paired `--front` and `--back` arguments to the live smoke test for
  repeatable preview, cancellation, approved-addition and duplicate checks.

- Added the MIT License, copyright 2026 Gleb Tanaka, and updated license references.

- Prepared private GitHub collaboration, contribution guidance and ignore rules
  for personal notes, editor settings and generated test/build output.
- Reviewed all historical source blobs for common credentials and personal
  configuration patterns; Git commit metadata uses a GitHub no-reply address.

- Prepared a generic standalone copy with fresh history for potential sharing.
- Replaced personal deck/model constants with ignored local JSON settings.
- Retained fixed-destination writes, duplicate checks and native Mac approval.
- Generated tunnel profile paths from the project location, with quoting for
  spaces and Unicode; kept keys as environment references.
- Added configuration and launcher-path tests: 14 unit tests pass using the
  prototype's existing Python environment; subsequently passed again in a fresh
  project environment. See TESTING.md for completed live checks.
- Added generic setup, architecture, migration, rollback and ideas documentation.

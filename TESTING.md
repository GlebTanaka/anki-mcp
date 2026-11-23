# Testing and validation

## Verified scope — 2026-11-17

The generic project was tested on Apple Silicon macOS with Python 3.14 and
MCP 1.30.0. No real deck names, account IDs, note IDs or runtime credentials are
included in this report.

| Check | Result | Evidence |
|---|---|---|
| Fresh virtual environment and locked dependency installation | Passed | User installed in the generic project and provided test output |
| Unit tests | 14 passed | Configuration/path tests and mocked card safety tests |
| Local MCP discovery, deck listing and preview | Passed | User supplied successful smoke-test output |
| Cancel native confirmation | No addition | User supplied cancelled / added=false result |
| Approve native confirmation | Note added | User supplied successful addition result |
| Repeat preview after addition | Rejected, no preview ID | User supplied duplicate result |
| Codex Desktop using a separate generic-server registration | Read tools passed | User supplied fresh-chat deck listing and existing-note preview result |
| Generic tunnel health and readiness | HTTP 200 | Directly checked local endpoints |
| Connected ChatGPT plugin through generic tunnel | Read tools passed | Direct plugin calls returned decks and expected duplicate result |
| Plugin addition with native Mac approval | Note added | Direct plugin call returned successful addition |
| Repeat plugin preview | Duplicate rejected | Direct plugin call returned no usable preview ID |

## Reproduce offline tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m unittest -v
```

Offline tests mock Anki and approval. They cover cancellation/failure, expired
and consumed previews, duplicate changes, fixed configuration, HTML restrictions,
read-only tool exposure, and generated paths containing spaces and Unicode.

## Live checks

Follow the README to configure an existing deck and note type. First run the
smoke test without `--add`. Use custom Front/Back text absent from your collection.
Repeat with `--add` and cancel. Preview again, then repeat with `--add` and
personally approve. A final preview should reject the duplicate. These tests
can create real notes; never automate approval or run them in unattended CI.

Test each AI client separately. Reuse one tunnel ID only after stopping the
previous client. Refresh ChatGPT tools after changing modes, and obtain new
preview IDs after a server restart.

## Tunnel controller (`tunnelctl.py`) — live check 2026-11-23

Run from a separate worktree on Apple Silicon macOS with tunnel-client v0.0.21,
the existing tunnel and ChatGPT plugin. No other client was running.

| Check | Result | Evidence |
|---|---|---|
| Unit tests in a fresh worktree environment | 27 passed | User-supplied output |
| `key set`, then `status` | Key stored in Keychain; Anki reachable | User-supplied output |
| `start` (read-only) and `start --enable-writes` after `stop` | Ready within the timeout | User-supplied output |
| Runtime key in the MCP server's environment | Absent; present only in the tunnel client | Direct `ps -E` check by name, value never printed |
| Single client for the tunnel during the run | One `tunnel-client run` process | Direct process check |
| ChatGPT plugin with additions enabled | Works, including human-approved additions | User report |
| One-click applet Status | Showed ready, mode, key, Anki and session state | User screenshot |

Not separately confirmed live: refusal of a second `start` while running, and
Stop from the applet. Both are covered by the offline tests, with Stop also
exercised through the same `do shell script` path using a fake client.

### Offline coverage

27 unit tests pass, 13 of them new. The lifecycle tests run a fake tunnel client
from a folder whose name has spaces and Japanese characters. They cover readiness,
duplicate refusal, stop, stale or reused PIDs, failed preflight, early exit with
key redaction, and refusing additions without a desktop session. A real
Keychain round trip used a throwaway item that was then deleted. The applet's
path handling was run headlessly.

## Limitations

This is a prototype, not a claim of universal compatibility. The fresh install
was in a new environment on the same Mac, not on an unrelated clean machine.
The generic Codex registration was tested read-only; its write path was not
separately exercised from a Codex chat. Local and tunnel write paths were tested.
Other Python/OS versions, unattended operation and alternative note fields are
not verified. The final plugin insertion was confirmed by tool result and a
subsequent duplicate check, not an independent Anki UI/render review.

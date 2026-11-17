# Contributing

This project is a prototype licensed under the [MIT License](LICENSE).
Public release remains pending.

## Development

Create an isolated Python environment and install `requirements.lock.txt` as
described in the README. Run `python -m unittest -v` from the project root.
These unit tests mock Anki and the approval dialog; they do not need an API key
or modify an Anki collection.

`smoke_test.py` talks to real Anki. Use an explicit test configuration and keep
it read-only first. `--add` can create a real note after human approval. Never
automate the native approval dialog or run live writes in unattended CI.

Preserve the fixed local endpoint, configured destination, duplicate checks,
preview expiry and confirmation behavior when changing code. Test changes to
configuration and paths, including spaces and Unicode.

## What belongs in Git

Commit code, generic documentation, tests and example configuration. Keep real
settings, credentials, generated media, installed tools and personal working
notes out of Git. Use `local-notes/` or `*.local.md` for private notes. Do not
put runtime keys or full credential screenshots in issues or pull requests.

Before committing, inspect `git diff --cached` and run
`git diff --cached --check`. Ignore rules do not hide sensitive content in
already tracked files or erase it from history. If a credential is exposed,
revoke it before addressing repository history.

Record proposals in IDEAS.md and meaningful delivered changes in CHANGELOG.md.

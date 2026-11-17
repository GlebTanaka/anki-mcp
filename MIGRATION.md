# Migration from an existing installation

This generic copy is prepared separately. Do not change a working installation
until this version has been tested with its own configuration.

1. Back up the existing source, ignored local profiles, Codex configuration and
   Anki collection separately. Preserve the old folder as a rollback option.
2. Create a new `.venv` here and install the locked dependencies. Do not copy an
   old virtual environment or link the running project to this one.
3. Create `config.local.json` using the existing exact deck and note-type names.
   Keep `Front`, `Back` fields; no card migration is needed because the data
   remains in Anki. Unit tests must pass before live checks.
4. Run the read-only smoke test against open Anki, then deliberately test an
   approved addition. These checks passed in the documented test environment;
   repeat them for your own installation before switching clients.
5. Record the old Codex launch arguments and replace them with this project's
   interpreter/script paths. Use a fresh chat, verify tools and the destination.
6. Stop the existing tunnel before reusing its ID. Install the official client
   here and put the existing tunnel ID in `tunnel.local.json`. Reuse the valid
   runtime key at the hidden prompt; do not copy it into files.
7. Run read-only first. The launcher generates paths from its own location.
   The existing Platform tunnel and ChatGPT plugin can be reused if associated
   with the same account/workspace. Refresh plugin tools and test from ChatGPT.
8. Restart with `--enable-writes`, refresh tools, preview again, then approve a
   test addition. Check the saved note and duplicate behavior.
9. Only retire the old installation after both clients pass. Keep any future
   startup service pointed at one installation, never both.

Rollback: stop the new tunnel, restore the previous Codex launch arguments,
restart the old tunnel from its original folder, refresh tools and create fresh
previews. Existing Anki notes are unaffected by which source folder is used.

Moving this generic project again requires a new virtual environment, updated
Codex paths and a restarted tunnel. Local tunnel paths regenerate automatically.

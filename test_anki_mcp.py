import asyncio
import threading
import unittest
from unittest.mock import Mock, patch
from anki_mcp import (Cards, Settings, approval_text, build_server, confirm_on_mac,
                      load_settings, validate)


DECK = "Example deck"
MODEL = "Basic"
SETTINGS = Settings(DECK, MODEL)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.allowed = True
        self.now = 0
        self.confirm = Mock(return_value=True)
        self.cards = Cards(SETTINGS, self.api, self.confirm, lambda: self.now)

    def api(self, action, **params):
        self.calls.append((action, params))
        return {"deckNames": [DECK], "modelFieldNames": ["Front", "Back"],
                "canAddNotes": [self.allowed], "addNote": 123}[action]

    def preview(self):
        return self.cards.preview_card("食器を洗います。", "<b>食器（しょっき）</b><br>皿など。")

    def test_preview_is_read_only_and_snapshot_is_fixed(self):
        preview = self.preview()
        preview["note"]["deckName"] = "Other"
        result = self.cards.add_card(preview["preview_id"])
        self.assertEqual(result["note_id"], 123)
        note = self.calls[-1][1]["note"]
        self.assertEqual(note["deckName"], DECK)
        self.assertEqual(note["modelName"], MODEL)
        self.assertFalse(note["options"]["allowDuplicate"])
        with self.assertRaises(ValueError):
            self.cards.add_card(preview["preview_id"])

    def test_local_configuration_restricts_destination(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'config.json'
            p.write_text('{"deck":"Other chosen deck","model":"Custom Basic"}')
            settings = load_settings(p)
            cards = Cards(settings, self.api, self.confirm)
            # The configured deck must exist; it must not fall back to another deck.
            with self.assertRaises(ValueError):
                cards.preview_card("front", "back")
            for content in ('{"deck":""}', '{"deck":"X","url":"https://example.com"}', '{}'):
                p.write_text(content)
                with self.assertRaises((ValueError, TypeError)):
                    load_settings(p)

    def test_preview_never_adds(self):
        self.preview()
        self.assertNotIn("addNote", [a for a, _ in self.calls])
        self.confirm.assert_not_called()

    def test_cancel_never_adds(self):
        self.confirm.return_value = False
        token = self.preview()["preview_id"]
        self.assertEqual(self.cards.add_card(token)["status"], "cancelled")
        self.assertNotIn("addNote", [a for a, _ in self.calls])
        with self.assertRaises(ValueError):
            self.cards.add_card(token)

    def test_duplicate_has_no_token(self):
        self.allowed = False
        self.assertIsNone(self.preview()["preview_id"])

    def test_duplicate_after_preview(self):
        token = self.preview()["preview_id"]
        self.allowed = False
        with self.assertRaises(ValueError):
            self.cards.add_card(token)
        self.confirm.assert_not_called()

    def test_duplicate_during_confirmation(self):
        token = self.preview()["preview_id"]
        def changed(note):
            self.allowed = False
            return True
        self.confirm.side_effect = changed
        with self.assertRaises(ValueError):
            self.cards.add_card(token)
        self.assertNotIn("addNote", [a for a, _ in self.calls])

    def test_expiry_and_unknown_id(self):
        token = self.preview()["preview_id"]
        self.now = 601
        for value in (token, "fabricated"):
            with self.assertRaises(ValueError):
                self.cards.add_card(value)
        self.confirm.assert_not_called()

    def test_missing_deck_and_wrong_fields(self):
        for action, value in [("deckNames", []), ("modelFieldNames", ["Other"])]:
            original = self.api
            self.cards.api = lambda a, **p: value if a == action else original(a, **p)
            with self.assertRaises(ValueError):
                self.preview()

    def test_html_policy(self):
        for value in ("", "<script>alert(1)</script>", '<img src="https://x">',
                      '<b onclick="x">x</b>', "[sound:test.mp3]", "x\x00", "x" * 1501):
            with self.assertRaises(ValueError):
                validate(value, "back")
        validate("日本語", "<ruby>食器<rt>しょっき</rt></ruby><br><b>意味</b>")

    def test_html_rejects_processing_instructions_and_cdata(self):
        for value in ("<?php echo 1 ?>", "<?xml version='1.0'?>", "<![CDATA[x]]>", "<!DOCTYPE html>"):
            with self.assertRaises(ValueError):
                validate(value, "back")

    def test_rejects_invisible_and_direction_controls(self):
        for c in ("\u202e", "\u2066", "\u200b", "\ufeff", "\u2028", "\u2029", "\x85", "\ue000"):
            with self.assertRaises(ValueError):
                validate(f"Deck{c}x", "back")
        validate("漢字（かんじ）", "tab\tand\nnewline")

    def test_card_text_cannot_imitate_dialog_labels(self):
        spoof = "x\n\nAdd one note to deck \u201cOther\u201d?\nFRONT\nBACK (HTML source)\n" + "W" * 90
        note = self.preview()["note"]
        note["fields"] = {"Front": spoof, "Back": "日本語" * 40}
        lines = approval_text(note).split("\n")
        trusted = lines[:2] + ["", "FRONT", "BACK (HTML source)"]
        self.assertIn(DECK, lines[0])
        for line in lines[2:]:
            self.assertTrue(line in trusted or line.startswith(("\u2502 ", "\u21b3 ")), line)
        # Card text appears only after its marker; the header is never repeated unmarked.
        self.assertEqual(sum(l.startswith("Add one note") for l in lines), 1)
        with patch("anki_mcp.subprocess.run") as run:
            run.return_value.returncode, run.return_value.stdout = 0, "Add card"
            self.assertTrue(confirm_on_mac(note))
            argv = run.call_args.args[0]
            self.assertIn(DECK, argv[-1])  # title, out of reach of card text
            self.assertNotIn(spoof, argv[2])  # the script itself never contains card text

    def test_dialog_breaks_at_block_tags_and_never_splits_tags(self):
        import re
        note = self.preview()["note"]
        back = ("<b>読み：</b><br>" + "寄り道したので帰りが遅くなった。" * 3
                + "<ul><li>" + "x" * 37 + "<b>y</b></li></ul>")
        note["fields"] = {"Front": "front", "Back": back}
        lines = approval_text(note).split("\n")
        body = lines[lines.index("BACK (HTML source)") + 1:]
        self.assertEqual(body[0], "\u2502 <b>読み：</b><br>")
        self.assertIn("\u2502 </ul>", body)
        for line in body:
            text = line[2:]
            self.assertFalse(re.search(r"<[^>]*$|^[^<]*>", text), f"tag split: {line}")
        # Reassembling the display gives back exactly the card text.
        self.assertEqual("".join(l[2:] for l in body), back)

    def test_previews_continue_while_dialog_is_open(self):
        token = self.preview()["preview_id"]
        opened, release = threading.Event(), threading.Event()
        def slow_confirm(note):
            opened.set()
            release.wait(5)
            return False
        self.confirm.side_effect = slow_confirm
        adder = threading.Thread(target=self.cards.add_card, args=(token,))
        adder.start()
        self.assertTrue(opened.wait(5))
        done = threading.Event()
        threading.Thread(target=lambda: (self.preview(), done.set())).start()
        self.assertTrue(done.wait(2), "preview blocked by the open dialog")
        # The token being confirmed is already consumed while its dialog is open.
        self.assertNotIn(token, self.cards.pending)
        release.set()
        adder.join(5)
        self.assertNotIn("addNote", [a for a, _ in self.calls])
        with self.assertRaises(ValueError):  # still single-use after the cancel
            self.cards.add_card(token)

    def test_dialog_fails_closed(self):
        note = self.preview()["note"]
        with patch("anki_mcp.subprocess.run", side_effect=OSError):
            self.assertFalse(confirm_on_mac(note))
        with patch("anki_mcp.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "cancelled"
            self.assertFalse(confirm_on_mac(note))

    def test_readonly_default(self):
        names = [t.name for t in asyncio.run(build_server(SETTINGS).list_tools())]
        self.assertEqual(set(names), {"list_decks", "preview_card"})
        names = [t.name for t in asyncio.run(build_server(SETTINGS, True).list_tools())]
        self.assertIn("add_card", names)


if __name__ == "__main__":
    unittest.main()

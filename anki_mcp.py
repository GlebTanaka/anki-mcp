"""Local-only Anki MCP prototype. stdout is reserved for MCP."""
import argparse
import copy
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

@dataclass(frozen=True)
class Settings:
    deck: str
    model: str = "Basic"

    def __post_init__(self):
        for value in (self.deck, self.model):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Configure a non-empty deck and model name")


def load_settings(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {"deck", "model"}:
        raise ValueError("Configuration accepts only deck and model")
    return Settings(**data)

URL = "http://127.0.0.1:8765"
TTL = 600


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("AnkiConnect redirects are not allowed")


def anki(action, **params):
    payload = {"action": action, "version": 6, "params": params}
    if os.environ.get("ANKICONNECT_API_KEY"):
        payload["key"] = os.environ["ANKICONNECT_API_KEY"]
    request = urllib.request.Request(
        URL, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        {"Content-Type": "application/json"}, method="POST")
    # Never route even localhost through an environment-configured proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            data = json.load(response)
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError("Cannot reach local AnkiConnect. Open Anki and check port 8765. "
                         "If this was an add, check Anki before retrying; it may have succeeded.") from exc
    if not isinstance(data, dict) or set(data) != {"result", "error"}:
        raise ValueError("Unexpected AnkiConnect response")
    if data["error"] is not None:
        raise ValueError(f"AnkiConnect: {data['error']}")
    return data["result"]


class SafeHTML(HTMLParser):
    """Small formatting subset; reject active content, attributes and media."""
    allowed = {"b", "strong", "i", "em", "u", "br", "p", "div", "ul", "ol", "li", "ruby", "rt", "rp"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed or attrs:
            raise ValueError("Use simple formatting HTML without attributes or media")

    def handle_endtag(self, tag):
        if tag not in self.allowed:
            raise ValueError("Unsupported HTML tag")

    def handle_comment(self, data):
        raise ValueError("HTML comments are not supported")

    def handle_decl(self, decl):
        raise ValueError("HTML declarations are not supported")


def validate(front, back):
    for value in (front, back):
        if not isinstance(value, str) or not value.strip() or len(value) > 1500:
            raise ValueError("Front and Back must each contain 1–1500 characters")
        if any(ord(c) < 32 and c not in "\n\t" for c in value) or "[sound:" in value.lower():
            raise ValueError("Control characters and media are not supported")
        parser = SafeHTML()
        parser.feed(value)
        parser.close()


def confirm_on_mac(note):
    # Card content is an argv value, never interpolated into executable AppleScript.
    text = (f"Add one Anki note?\nDeck: {note['deckName']}\nType: {note['modelName']}\n\n"
            f"Front:\n{note['fields']['Front']}\n\nBack (HTML source):\n{note['fields']['Back']}")
    script = '''on run argv
set answer to display dialog (item 1 of argv) with title "Anki card approval" buttons {"Cancel", "Add card"} default button "Cancel" cancel button "Cancel" giving up after 45
if gave up of answer then return "cancelled"
return button returned of answer
end run'''
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script, text],
                                capture_output=True, text=True, timeout=50)
        return result.returncode == 0 and result.stdout.strip() == "Add card"
    except (OSError, subprocess.TimeoutExpired):
        return False


class Cards:
    def __init__(self, settings, api=anki, confirm=confirm_on_mac, clock=time.monotonic):
        self.settings = settings
        self.api, self.confirm, self.clock = api, confirm, clock
        self.pending = {}
        self.lock = threading.Lock()

    def list_decks(self):
        return self.api("deckNames")

    def check(self, note):
        if self.settings.deck not in self.list_decks():
            raise ValueError(f"Required deck does not exist: {self.settings.deck}")
        fields = self.api("modelFieldNames", modelName=self.settings.model)
        if fields != ["Front", "Back"]:
            raise ValueError("Expected note type fields in order: Front, Back")
        allowed = self.api("canAddNotes", notes=[note])
        if not isinstance(allowed, list) or len(allowed) != 1 or type(allowed[0]) is not bool:
            raise ValueError("Invalid duplicate-check response")
        return allowed[0]

    def preview_card(self, front, back):
        validate(front, back)
        note = {"deckName": self.settings.deck, "modelName": self.settings.model,
                "fields": {"Front": front, "Back": back},
                "options": {"allowDuplicate": False, "duplicateScope": "collection"},
                "tags": ["codex-anki"]}
        with self.lock:
            now = self.clock()
            self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
            can_add = self.check(note)
            token = None
            if can_add:
                if len(self.pending) >= 100:
                    raise ValueError("Too many pending previews; wait for expiry or restart")
                token = secrets.token_urlsafe(24)
                self.pending[token] = (now + TTL, copy.deepcopy(note))
            return {"note": note, "can_add": can_add, "preview_id": token,
                    "expires_in_seconds": TTL if token else 0,
                    "status": "ready_for_review" if can_add else "duplicate_or_invalid"}

    def add_card(self, preview_id):
        with self.lock:
            item = self.pending.pop(preview_id, None)
            if item is None or item[0] <= self.clock():
                raise ValueError("Unknown, consumed, or expired preview. Preview again.")
            _, note = item
            if not self.check(note):
                raise ValueError("Duplicate or invalid note; nothing added")
            if not self.confirm(note):
                return {"status": "cancelled", "added": False}
            if item[0] <= self.clock() or not self.check(note):
                raise ValueError("Preview expired or note became duplicate; nothing added")
            # addNote repeats Anki's own duplicate check, including concurrent changes.
            note_id = self.api("addNote", note=note)
            if type(note_id) is not int:
                raise ValueError("Unexpected add result. Check Anki before retrying.")
            return {"status": "added", "note_id": note_id, "deck": self.settings.deck}


def build_server(settings, enable_writes=False):
    cards = Cards(settings)
    mcp = FastMCP("anki-local")
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    @mcp.tool(annotations=read)
    def list_decks() -> list[str]:
        """List Anki decks. Does not modify the collection."""
        return cards.list_decks()

    @mcp.tool(annotations=read)
    def preview_card(front: str, back: str) -> dict:
        """Read-only Anki check and preview. Show exact fields to user. Simple HTML allowed.
        Returns an expiring preview_id; does not insert a note. Destination is restricted to the locally configured deck.
        """
        return cards.preview_card(front, back)

    if enable_writes:
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=False, openWorldHint=False))
        def add_card(preview_id: str) -> dict:
            """Add the exact preview after the user requests it. Opens a local macOS
            confirmation dialog; only the human should click Add card. Never automate
            that dialog. Preview ID is single-use; duplicate checks run again.
            """
            return cards.add_card(preview_id)
    return mcp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-writes", action="store_true")
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).with_name("config.local.json"))
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f"Cannot load deck configuration: {exc}. Copy config.example.json to config.local.json and edit it.")
    build_server(settings, args.enable_writes).run(transport="stdio")

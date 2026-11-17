"""Real MCP stdio client; default is read-only. --add requests a human dialog."""
import argparse
import asyncio
import json
import sys
import os
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FRONT = "食事の後で、食器を洗います。"
BACK = "<b>読み方</b><br>しょくじの あとで、しょっきを あらいます。<br><br><b>意味</b><br>ご飯を食べてから、皿や茶碗などを洗います。"

async def main(add=False):
    args = [str(Path(__file__).with_name("anki_mcp.py"))]
    if add:
        args.append("--enable-writes")
    async with stdio_client(StdioServerParameters(command=sys.executable, args=args, env={k: v for k, v in os.environ.items() if k == "ANKICONNECT_API_KEY"})) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("Tools:", names)
            assert set(names) == ({"list_decks", "preview_card", "add_card"} if add else {"list_decks", "preview_card"})
            for name, arguments in [("list_decks", {}), ("preview_card", {"front": FRONT, "back": BACK})]:
                result = await session.call_tool(name, arguments)
                print(name, result.model_dump_json())
                assert not result.isError
            preview = json.loads(result.content[0].text)
            if add and preview["preview_id"]:
                result = await session.call_tool("add_card", {"preview_id": preview["preview_id"]})
                print("add_card", result.model_dump_json())
                assert not result.isError

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", action="store_true")
    asyncio.run(main(parser.parse_args().add))

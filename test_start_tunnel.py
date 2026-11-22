import json
import shlex
import tempfile
import unittest
from pathlib import Path
from start_tunnel import KEY_ENV, load_tunnel_id, make_profile


class ProfileTests(unittest.TestCase):
    def test_paths_with_spaces_and_unicode_and_modes(self):
        root = Path('/tmp/Example Project/日本語')
        for writes in (False, True):
            profile = make_profile(root, 'tunnel_example', writes)
            argv = shlex.split(profile['mcp']['commands'][0]['command'])
            # The MCP server and its approval dialog never inherit the runtime key.
            self.assertEqual(argv[:3], ['/usr/bin/env', '-u', KEY_ENV])
            self.assertEqual(argv[3:5], [str(root / '.venv/bin/python'), str(root / 'anki_mcp.py')])
            self.assertEqual(argv[5:], ['--enable-writes'] if writes else [])
            self.assertEqual(profile['health']['listen_addr'], '127.0.0.1:0')
            self.assertEqual(profile['control_plane']['api_key'], 'env:CONTROL_PLANE_API_KEY')

    def test_invalid_tunnel_id(self):
        for value in ('', None, 'https://example.com', 'tunnel_bad;command'):
            with self.assertRaises(ValueError):
                make_profile(Path('/tmp/project'), value)

    def test_tunnel_settings_accept_only_tunnel_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for content in ('{}', '{"tunnel_id": "tunnel_a", "key": "x"}', '{"tunnel_id": "bad"}'):
                (root / 'tunnel.local.json').write_text(content)
                with self.assertRaises(ValueError):
                    load_tunnel_id(root)
            (root / 'tunnel.local.json').write_text(json.dumps({'tunnel_id': 'tunnel_a1'}))
            self.assertEqual(load_tunnel_id(root), 'tunnel_a1')

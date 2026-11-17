import shlex
import unittest
from pathlib import Path
from start_tunnel import make_profile


class ProfileTests(unittest.TestCase):
    def test_paths_with_spaces_and_unicode_and_modes(self):
        root = Path('/tmp/Example Project/日本語')
        for writes in (False, True):
            profile = make_profile(root, 'tunnel_example', writes)
            argv = shlex.split(profile['mcp']['commands'][0]['command'])
            self.assertEqual(argv[:2], [str(root / '.venv/bin/python'), str(root / 'anki_mcp.py')])
            self.assertEqual(argv[2:], ['--enable-writes'] if writes else [])
            self.assertEqual(profile['health']['listen_addr'], '127.0.0.1:0')
            self.assertEqual(profile['control_plane']['api_key'], 'env:CONTROL_PLANE_API_KEY')

    def test_invalid_tunnel_id(self):
        for value in ('', None, 'https://example.com', 'tunnel_bad;command'):
            with self.assertRaises(ValueError):
                make_profile(Path('/tmp/project'), value)

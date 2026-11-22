"""Offline tests for tunnelctl. A fake tunnel client stands in for the real one;
no real tunnel, Keychain item, network service or Anki collection is touched."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import tunnelctl
from tunnelctl import Keychain, Tunnel, TunnelError, config_path_from

TUNNEL = 'tunnel_test123'
KEY = 'sk-test_' + 'x' * 30

FAKE_CLIENT = textwrap.dedent('''\
    #!{python}
    import http.server, json, os, sys, threading
    args = sys.argv[1:]
    if args[0] == 'doctor':
        sys.exit(int(os.environ.get('FAKE_DOCTOR_FAIL', '0')))
    if os.environ.get('FAKE_EXIT'):
        print('boom ' + os.environ.get('CONTROL_PLANE_API_KEY', ''), flush=True)
        sys.exit(3)
    url_file = args[args.index('--health.url-file') + 1]
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path in ('/healthz', '/readyz') else 404)
            self.end_headers()
        def log_message(self, *a):
            pass
    server = http.server.HTTPServer(('127.0.0.1', 0), H)
    with open(url_file + '.env', 'w') as f:
        json.dump({{'has_key': os.environ.get('CONTROL_PLANE_API_KEY') == {key!r}}}, f)
    with open(url_file, 'w') as f:
        f.write('http://127.0.0.1:%d' % server.server_port)
    server.serve_forever()
''')


class FakeKeychain:
    def __init__(self, key=KEY):
        self.items = {TUNNEL: key} if key else {}

    def get(self, account):
        return self.items.get(account)

    def set(self, account, key):
        self.items[account] = key


def make_root(folder):
    root = Path(folder) / 'Project with spaces' / '日本語'
    (root / '.tools/tunnel-client').mkdir(parents=True)
    (root / '.venv/bin').mkdir(parents=True)
    (root / '.venv/bin/python').write_text('')
    (root / 'config.local.json').write_text('{"deck": "Example", "model": "Basic"}')
    (root / 'tunnel.local.json').write_text(json.dumps({'tunnel_id': TUNNEL}))
    client = root / '.tools/tunnel-client/tunnel-client'
    client.write_text(FAKE_CLIENT.format(python=sys.executable, key=KEY))
    client.chmod(0o700)
    return root


def only_under(root):
    """Process lister that ignores everything outside the test folder."""
    return lambda: [(pid, cmd) for pid, cmd in tunnelctl.list_processes() if str(root) in cmd]


class KeychainTests(unittest.TestCase):
    def test_key_never_in_argv(self):
        calls = []
        def run(argv, **kw):
            calls.append((argv, kw.get('input')))
            stdout = KEY + '\n' if 'find-generic-password' in argv else ''
            return subprocess.CompletedProcess(argv, 0, stdout, '')
        Keychain(run=run).set(TUNNEL, KEY)
        for argv, _ in calls:
            self.assertNotIn(KEY, ' '.join(argv))
        self.assertIn(KEY, calls[0][1])

    def test_rejects_unsafe_key_and_reports_missing(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 44, '', '')
        chain = Keychain(run=run)
        for bad in ('', 'short', KEY + ' -T /bin/sh', KEY + '\ndelete-keychain', 'x"' * 20):
            with self.assertRaises(TunnelError):
                chain.set(TUNNEL, bad)
        self.assertIsNone(chain.get(TUNNEL))
        with self.assertRaises(ValueError):
            chain.get('tunnel_bad;x')


class ProcessTests(unittest.TestCase):
    def test_config_path_with_spaces(self):
        cmd = '/a b/tunnel-client run --config /x y/日本/managed.yaml --health.url-file /z'
        self.assertEqual(config_path_from(cmd), Path('/x y/日本/managed.yaml'))

    def test_other_clients_detects_same_and_unknown_tunnels(self):
        with tempfile.TemporaryDirectory() as folder:
            same = Path(folder) / 'same.yaml'
            same.write_text(f'tunnel_id: {TUNNEL}\n')
            other = Path(folder) / 'other.yaml'
            other.write_text('tunnel_id: tunnel_someoneelse\n')
            envref = Path(folder) / 'envref.yaml'
            envref.write_text('tunnel_id: env:SOME_VAR\n')
            procs = [(11, f'/x/tunnel-client run --config {same}'),
                     (18, f'/x/tunnel-client run --config {envref}'),
                     (12, f'/x/tunnel-client run --config {other}'),
                     (13, '/x/tunnel-client run --config /missing.yaml'),
                     (14, f'/x/tunnel-client doctor --config {same}'),
                     (15, f'/x/tunnel-client run --control-plane.tunnel-id {TUNNEL}'),
                     (16, 'vim tunnel-client.md'),
                     (17, f"zsh -c cp .tools/tunnel-client/tunnel-client x; run {TUNNEL}")]
            tunnel = Tunnel(folder, FakeKeychain(), processes=lambda: procs)
            self.assertEqual(tunnel.other_clients(TUNNEL),
                             [(11, 'same tunnel'), (18, 'unknown tunnel'),
                              (13, 'unknown tunnel'), (15, 'same tunnel')])
            with self.assertRaises(TunnelError):
                tunnel.ensure_no_other_client(TUNNEL)

    def test_loopback_only(self):
        for url in ('http://example.com/readyz', 'https://127.0.0.1/readyz', 'file:///etc/passwd'):
            self.assertIsNone(tunnelctl.loopback_get(url))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(self.tmp.name)
        self.keychain = FakeKeychain()
        self.tunnel = self.new_tunnel()

    def tearDown(self):
        self.tunnel.stop()
        self.tmp.cleanup()

    def new_tunnel(self, gui=True):
        return Tunnel(self.root, self.keychain, processes=only_under(self.root),
                      is_gui=lambda: gui, ready_timeout=10)

    def test_start_ready_duplicate_stop(self):
        result = self.tunnel.start()
        self.assertEqual((result['mode'], result['ready']), ('read-only', True))
        env = json.loads(Path(str(self.tunnel.health_file) + '.env').read_text())
        self.assertTrue(env['has_key'])
        self.assertNotIn(KEY, self.tunnel.profile.read_text())
        self.assertEqual(oct(self.tunnel.profile.stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct(self.tunnel.run_dir.stat().st_mode & 0o777), '0o700')
        status = self.tunnel.status()
        self.assertTrue(status['ready'])
        self.assertEqual(status['other_clients'], [])

        # A second controller (a second click, another Terminal) must refuse.
        for writes in (False, True):
            with self.assertRaisesRegex(TunnelError, 'already running'):
                self.new_tunnel().start(enable_writes=writes)
        # So must the foreground launcher's guard, even without our state file.
        self.tunnel.state_file.rename(self.tunnel.run_dir / 'moved.json')
        with self.assertRaisesRegex(TunnelError, 'Another tunnel client'):
            self.new_tunnel().ensure_no_other_client(TUNNEL)
        (self.tunnel.run_dir / 'moved.json').rename(self.tunnel.state_file)

        self.assertTrue(self.tunnel.stop())
        self.assertFalse(self.tunnel.status()['running'])
        self.assertFalse(self.tunnel.health_file.exists())
        self.assertFalse(self.tunnel.stop())

    def test_writes_mode_is_explicit_and_needs_desktop_session(self):
        with self.assertRaisesRegex(TunnelError, 'desktop session'):
            self.new_tunnel(gui=False).start(enable_writes=True)
        self.assertFalse(self.tunnel.state_file.exists())
        result = self.tunnel.start(enable_writes=True)
        self.assertEqual(result['mode'], 'additions')
        argv = shlex.split(json.loads(self.tunnel.profile.read_text())
                           ['mcp']['commands'][0]['command'])
        self.assertEqual(argv[-1], '--enable-writes')

    def test_missing_key_noninteractive_starts_nothing(self):
        self.keychain.items.clear()
        with self.assertRaisesRegex(TunnelError, 'key set'):
            self.tunnel.start(interactive=False)
        self.assertFalse(self.tunnel.state_file.exists())

    def test_failed_doctor_and_early_exit_redact_key(self):
        with patch.dict(os.environ, {'FAKE_DOCTOR_FAIL': '1'}):
            with self.assertRaisesRegex(TunnelError, 'Preflight'):
                self.tunnel.start()
        with patch.dict(os.environ, {'FAKE_EXIT': '1'}):
            with self.assertRaises(TunnelError) as caught:
                self.tunnel.start()
        self.assertIn('[redacted]', str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertFalse(self.tunnel.state_file.exists())

    def test_stale_state_and_pid_reuse_are_not_trusted(self):
        self.tunnel.run_dir.mkdir(mode=0o700)
        # Our own pid is alive but is not the tunnel client: never signal it.
        self.tunnel.write_state({'pid': os.getpid(), 'started': 'x', 'mode': 'read-only'})
        self.assertFalse(self.tunnel.status()['running'])
        self.assertFalse(self.tunnel.stop())
        self.assertTrue(self.tunnel.start()['ready'])

    def test_concurrent_start_is_serialized(self):
        with self.tunnel.locked():
            with self.assertRaisesRegex(TunnelError, 'in progress'):
                self.new_tunnel().start()


class AppTests(unittest.TestCase):
    def test_applet_source_has_no_paths_or_secrets(self):
        self.assertNotIn('/Users', tunnelctl.APPLESCRIPT)
        self.assertIn('Add card', tunnelctl.APPLESCRIPT)
        self.assertIn('default button "Cancel"', tunnelctl.APPLESCRIPT)


if __name__ == '__main__':
    unittest.main()

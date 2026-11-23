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
import time
import unittest
from unittest.mock import patch

import tunnelctl
from tunnelctl import Keychain, Tunnel, TunnelError, client_tunnel

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


def only_under(*roots):
    """Process lister that ignores everything outside the test folders."""
    def lister():
        return [(pid, argv) for pid, argv in tunnelctl.list_processes()
                if argv and any(str(root) in arg for root in roots for arg in argv)]
    return lister


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
    def test_reads_exact_argv_of_a_process(self):
        self.assertEqual(tunnelctl.process_argv(os.getpid())[1:], sys.orig_argv[1:])
        with tempfile.TemporaryDirectory() as folder:
            odd = Path(folder) / 'a dir --config x' / '日本語'
            odd.mkdir(parents=True)
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)',
                                      str(odd), '--flag=two words'])
            try:
                deadline = time.monotonic() + 5
                while tunnelctl.process_argv(child.pid) in (None, [sys.executable]):
                    if time.monotonic() > deadline:
                        break
                self.assertEqual(tunnelctl.process_argv(child.pid)[-2:],
                                 [str(odd), '--flag=two words'])
            finally:
                child.kill()
                child.wait()

    def test_client_tunnel_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            # A folder named like a tunnel ID must not hide the real one.
            base = Path(folder) / 'tunnel_unrelated' / 'with space'
            base.mkdir(parents=True)
            same = base / 'same.yaml'
            same.write_text(json.dumps({'control_plane': {'tunnel_id': TUNNEL}}))
            yaml = base / 'plain.yaml'
            yaml.write_text(f'control_plane:\n  tunnel_id: "{TUNNEL}"\n')
            client = str(base / 'tunnel-client')
            cases = [
                ([client, 'run', '--config', str(same)], (True, TUNNEL)),
                ([client, 'run', f'--config={yaml}'], (True, TUNNEL)),
                (['/usr/bin/python3', client, 'run', '--config', str(same)], (True, TUNNEL)),
                ([client, 'run', '--config', str(same), '--control-plane.tunnel-id', 'tunnel_b'],
                 (True, 'tunnel_b')),
                ([client, 'run'], (True, None)),                       # set by environment
                ([client, 'run', '--config', '/missing.yaml'], (True, None)),
                ([client, 'doctor', '--config', str(same)], (False, None)),
                (['zsh', '-c', f'{client} run --config {same}'], (False, None)),
            ]
            for argv, expected in cases:
                self.assertEqual(client_tunnel(argv), expected, argv)
            for text in ('tunnel_id: env:X', 'a:\n  tunnel_id: tunnel_a\nb:\n  tunnel_id: tunnel_b'):
                self.assertIsNone(tunnelctl.config_tunnel_id(text))

    def test_other_clients_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / 'other.yaml'
            other.write_text('tunnel_id: tunnel_someoneelse\n')
            same = Path(folder) / 'tunnel_unrelated.yaml'
            same.write_text(f'tunnel_id: {TUNNEL}\n')
            procs = [(11, ['/x/tunnel-client', 'run', '--config', str(same)]),
                     (12, ['/x/tunnel-client', 'run', '--config', str(other)]),
                     (13, ['/x/tunnel-client', 'run', '--config', '/missing.yaml']),
                     (14, None),                                   # argv unreadable
                     (15, ['vim', 'tunnel-client.md'])]
            tunnel = Tunnel(folder, FakeKeychain(), processes=lambda: procs, lock_dir=folder)
            self.assertEqual(tunnel.other_clients(TUNNEL),
                             [(11, 'same tunnel'), (13, 'unknown tunnel'),
                              (14, 'unreadable process')])
            with self.assertRaises(TunnelError):
                tunnel.ensure_no_other_client(TUNNEL)
        with patch('tunnelctl.ps', return_value=''):
            with self.assertRaisesRegex(TunnelError, 'Cannot inspect'):
                tunnelctl.list_processes()

    def test_loopback_only(self):
        for url in ('http://example.com/readyz', 'https://127.0.0.1/readyz', 'file:///etc/passwd'):
            self.assertIsNone(tunnelctl.loopback_get(url))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(self.tmp.name)
        self.keychain = FakeKeychain()
        self.lock_dir = Path(self.tmp.name) / 'locks'
        self.tunnel = self.new_tunnel()

    def tearDown(self):
        for tunnel in getattr(self, 'extra', []) + [self.tunnel]:
            tunnel.stop()
        self.tmp.cleanup()

    def new_tunnel(self, gui=True, root=None, processes=None):
        return Tunnel(root or self.root, self.keychain,
                      processes=processes or only_under(self.root),
                      is_gui=lambda: gui, ready_timeout=10, lock_dir=self.lock_dir)

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
        with self.tunnel.locked(TUNNEL):
            with self.assertRaisesRegex(TunnelError, 'in progress'):
                self.new_tunnel().start()

    def test_shared_lock_blocks_other_checkouts_without_process_scan(self):
        other_root = make_root(Path(self.tmp.name) / 'second checkout')
        self.assertTrue(self.tunnel.start()['ready'])
        # Even with a process scan that sees nothing, the per-tunnel lock refuses.
        blind = self.new_tunnel(root=other_root, processes=lambda: [])
        self.extra = [blind]
        with self.assertRaisesRegex(TunnelError, 'already running.*tunnelctl.py'):
            blind.start()
        status = blind.status()
        self.assertFalse(status['running'])
        self.assertTrue(status['client_lock_held'])
        # The lock belongs to the client: if it dies, the lock goes with it.
        os.killpg(self.tunnel.read_state()['pid'], 9)
        self.tunnel._proc.wait()
        self.assertTrue(blind.start()['ready'])

    def test_lock_survives_exec_like_the_foreground_launcher(self):
        code = textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {str(Path(__file__).parent)!r})
            from tunnelctl import Tunnel
            fd = Tunnel({str(self.root)!r}, lock_dir={str(self.lock_dir)!r}).acquire_client_lock(
                {TUNNEL!r}, 'start_tunnel.py')
            os.set_inheritable(fd, True)
            os.execv('/bin/sleep', ['sleep', '30'])
        """)
        child = subprocess.Popen([sys.executable, '-c', code])
        try:
            deadline = time.monotonic() + 10
            while not self.tunnel.client_lock_held(TUNNEL):
                self.assertLess(time.monotonic(), deadline)
            with self.assertRaisesRegex(TunnelError, 'already running.*start_tunnel.py'):
                self.new_tunnel(processes=lambda: []).start()
        finally:
            child.kill()
            child.wait()
        self.assertFalse(self.tunnel.client_lock_held(TUNNEL))


class AppTests(unittest.TestCase):
    def test_applet_source_has_no_paths_or_secrets(self):
        self.assertNotIn('/Users', tunnelctl.APPLESCRIPT)
        self.assertIn('Add card', tunnelctl.APPLESCRIPT)
        self.assertIn('default button "Cancel"', tunnelctl.APPLESCRIPT)


if __name__ == '__main__':
    unittest.main()

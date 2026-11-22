"""Start, stop and check a background Anki tunnel; keep its key in the Keychain.

Commands:
  key set | key delete | key status   manage the runtime key in macOS Keychain
  start [--enable-writes]             start in the background, wait for readiness
  stop                                stop the managed tunnel client
  restart                             stop, then start in the previous mode
  status [--json] [--check]           running state, readiness and prerequisites
  logs [-n N]                         tail of the managed client's log
  install-app [--dest DIR]            build a one-click "Anki Tunnel" applet

Card additions still need the human to click "Add card" in the Mac dialog that
anki_mcp.py opens; nothing here can approve, bypass or suppress that dialog.
"""
import argparse
import contextlib
import fcntl
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from start_tunnel import KEY_ENV, check_tunnel_id, load_tunnel_id, make_profile, write_profile

SERVICE = 'anki-mcp-tunnel'
SECURITY = '/usr/bin/security'
# Runtime keys are opaque tokens; refusing other characters keeps them safe to
# pass through `security -i` without quoting.
KEY_RE = re.compile(r'[A-Za-z0-9_\-]{20,512}')
NOT_FOUND = 44  # security(1) exit status for a missing item
APP_NAME = 'Anki Tunnel.app'


class TunnelError(Exception):
    pass


class Keychain:
    """Generic-password items in the login keychain, keyed by tunnel ID."""

    def __init__(self, service=SERVICE, run=subprocess.run):
        self.service, self.run = service, run

    def get(self, account):
        check_tunnel_id(account)
        result = self.run([SECURITY, 'find-generic-password', '-s', self.service,
                           '-a', account, '-w'], capture_output=True, text=True)
        if result.returncode == NOT_FOUND:
            return None
        if result.returncode:
            raise TunnelError('Keychain lookup failed or was denied')
        return result.stdout.rstrip('\n') or None

    def set(self, account, key):
        check_tunnel_id(account)
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise TunnelError('That does not look like a runtime API key; nothing saved')
        # The key goes through stdin, never argv, so `ps` cannot observe it.
        command = (f'add-generic-password -U -s {self.service} -a {account} '
                   f'-l "Anki MCP tunnel runtime key" -w {key}\n')
        self.run([SECURITY, '-i'], input=command, capture_output=True, text=True)
        if self.get(account) != key:
            raise TunnelError('Keychain did not store the key')

    def delete(self, account):
        check_tunnel_id(account)
        result = self.run([SECURITY, 'delete-generic-password', '-s', self.service,
                           '-a', account], capture_output=True, text=True)
        if result.returncode not in (0, NOT_FOUND):
            raise TunnelError('Keychain deletion failed')
        return result.returncode == 0


# A UTF-8 locale stops ps from escaping non-ASCII paths (e.g. 日本語 folders)
# and keeps its lstart format stable.
PS_ENV = {'LC_ALL': 'en_US.UTF-8', 'PATH': '/usr/bin:/bin'}


def ps(args):
    result = subprocess.run(['/bin/ps', *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', env=PS_ENV)
    return result.stdout if result.returncode == 0 else ''


def list_processes():
    out = ps(['-axo', 'pid=,command='])
    procs = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(' ')
        if pid.isdigit():
            procs.append((int(pid), command.strip()))
    return procs


PS_LINE = re.compile(r'^\s*(\S+)\s+(\S+\s+\S+\s+\d+\s+\S+\s+\d+)\s+(.*)$')


def inspect_pid(pid):
    """Return (stat, start time, command) for a live pid, or None."""
    match = PS_LINE.match(ps(['-p', str(pid), '-o', 'stat=,lstart=,command=']).strip())
    if not match or match.group(1).startswith('Z'):
        return None
    return match.groups()


def gui_session():
    """True when running in the logged-in user's Aqua session (dialogs can show)."""
    try:
        result = subprocess.run(['/bin/launchctl', 'managername'],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() == 'Aqua'


def loopback_get(url, timeout=2):
    """GET a loopback-only URL without proxies; return HTTP status or None."""
    parts = urllib.parse.urlsplit(url)
    try:
        if parts.scheme != 'http' or not ipaddress.ip_address(parts.hostname).is_loopback:
            return None
    except ValueError:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, ValueError):
        return None


def tunnel_ids(text):
    # 'tunnel_id' is the YAML key name, not an ID.
    return set(re.findall(r'tunnel_[A-Za-z0-9]+', text)) - {'tunnel_id'}


def config_path_from(command):
    match = re.search(r'--config[ =](.+?)(?= --|$)', command)
    return Path(match.group(1)) if match else None


class Tunnel:
    def __init__(self, root, keychain=None, processes=list_processes, inspect=inspect_pid,
                 is_gui=gui_session, ready_timeout=45, stop_timeout=10):
        self.root = Path(root)
        self.keychain = keychain or Keychain()
        self.processes, self.inspect, self.is_gui = processes, inspect, is_gui
        self.ready_timeout, self.stop_timeout = ready_timeout, stop_timeout
        self.client = self.root / '.tools/tunnel-client/tunnel-client'
        self.python = self.root / '.venv/bin/python'
        self.run_dir = self.root / '.tunnel-run'
        self.state_file = self.run_dir / 'state.json'
        self.log_file = self.run_dir / 'client.log'
        self.health_file = self.run_dir / 'health.url'
        self.profile = self.root / 'tunnel-profiles' / 'managed.yaml'

    # -- bookkeeping ---------------------------------------------------------

    @contextlib.contextmanager
    def locked(self):
        """Serialize start/stop so two clicks cannot launch two clients."""
        self.run_dir.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(self.run_dir / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TunnelError('Another start or stop is in progress') from None
            yield
        finally:
            os.close(fd)

    def read_state(self):
        try:
            state = json.loads(self.state_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        return state if isinstance(state, dict) and isinstance(state.get('pid'), int) else None

    def write_state(self, state):
        fd = os.open(self.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f)

    def clear_state(self):
        for path in (self.state_file, self.health_file):
            path.unlink(missing_ok=True)

    def alive(self, state):
        """The recorded pid is still the same tunnel-client process we started."""
        info = state and self.inspect(state['pid'])
        return bool(info and info[1] == state.get('started')
                    and str(self.client) in info[2])

    def running(self):
        state = self.read_state()
        if state and self.alive(state):
            return state
        return None

    # -- duplicate prevention -------------------------------------------------

    def other_clients(self, tunnel_id, exclude=()):
        """Other `tunnel-client run` processes that use, or may use, this tunnel."""
        found = []
        for pid, command in self.processes():
            if pid in exclude or pid == os.getpid():
                continue
            if not re.search(r'tunnel-client\s+run(\s|$)', command):
                continue
            ids = tunnel_ids(command)
            config = config_path_from(command)
            if not ids and config:
                try:
                    ids = tunnel_ids(config.read_text(errors='replace'))
                except OSError:
                    pass
            if not ids or tunnel_id in ids:
                found.append((pid, 'same tunnel' if ids else 'unknown tunnel'))
        return found

    def ensure_no_other_client(self, tunnel_id, exclude=()):
        state = self.running()
        if state:
            raise TunnelError(f"Managed tunnel already running (pid {state['pid']}, "
                              f"{state['mode']}); stop it first")
        others = self.other_clients(tunnel_id, exclude)
        if others:
            listed = ', '.join(f'pid {pid} ({why})' for pid, why in others)
            raise TunnelError(f'Another tunnel client may be using this tunnel: {listed}. '
                              'Stop it first; two clients must not share a tunnel ID.')

    # -- prerequisites --------------------------------------------------------

    def preflight(self, enable_writes):
        from anki_mcp import load_settings
        try:
            load_settings(self.root / 'config.local.json')
        except (OSError, ValueError, TypeError) as exc:
            raise TunnelError(f'Deck configuration problem: {exc}') from None
        try:
            tunnel_id = load_tunnel_id(self.root)
        except (OSError, ValueError, TypeError) as exc:
            raise TunnelError(str(exc)) from None
        if not os.access(self.client, os.X_OK):
            raise TunnelError('Install the executable tunnel client at .tools/tunnel-client/')
        if not self.python.is_file():
            raise TunnelError('Create .venv first (see README)')
        if enable_writes and not self.is_gui():
            # Without a GUI session the approval dialog cannot appear, so writes
            # would only ever fail closed. Refuse rather than look available.
            raise TunnelError('Card additions need the logged-in Mac desktop session '
                              'so the approval dialog can appear; start from there')
        return tunnel_id

    def obtain_key(self, tunnel_id, interactive):
        key = self.keychain.get(tunnel_id)
        if key:
            return key
        if not interactive:
            raise TunnelError('No runtime key in the Keychain; run: tunnelctl.py key set')
        key = getpass.getpass('Paste tunnel runtime API key (hidden): ').strip()
        if not key:
            raise TunnelError('No key supplied; nothing started')
        if input('Save it in the macOS Keychain for next time? [y/N] ').strip().lower() == 'y':
            self.keychain.set(tunnel_id, key)
        return key

    # -- lifecycle ------------------------------------------------------------

    def log_tail(self, lines=20, redact=None):
        try:
            text = self.log_file.read_text(errors='replace')
        except OSError:
            return ''
        if redact:
            text = text.replace(redact, '[redacted]')
        return '\n'.join(text.splitlines()[-lines:])

    def start(self, enable_writes=False, interactive=False):
        with self.locked():
            tunnel_id = self.preflight(enable_writes)
            stale = self.read_state()
            if stale and not self.alive(stale):
                self.clear_state()
            self.ensure_no_other_client(tunnel_id)
            key = self.obtain_key(tunnel_id, interactive)
            env = dict(os.environ, **{KEY_ENV: key})
            write_profile(self.profile, make_profile(self.root, tunnel_id, enable_writes))
            if self.log_file.exists():
                os.replace(self.log_file, self.log_file.with_suffix('.log.1'))
            fd = os.open(self.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'ab') as log:
                doctor = subprocess.run([str(self.client), 'doctor', '--config', str(self.profile),
                                         '--explain'], env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=120)
                if doctor.returncode:
                    raise TunnelError('Preflight (tunnel-client doctor) failed; nothing started.\n'
                                      + self.log_tail(redact=key))
                self.health_file.unlink(missing_ok=True)
                proc = subprocess.Popen(
                    [str(self.client), 'run', '--config', str(self.profile),
                     '--health.url-file', str(self.health_file)],
                    env=env, cwd=self.root, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
            del env
            info = self.inspect(proc.pid)
            mode = 'additions' if enable_writes else 'read-only'
            self.write_state({'pid': proc.pid, 'started': info[1] if info else None,
                              'mode': mode, 'tunnel_id': tunnel_id,
                              'started_at': time.time()})
            self._proc = proc
            ready = self.wait_ready(proc, key)
            del key
            return {'pid': proc.pid, 'mode': mode, 'ready': ready}

    def wait_ready(self, proc, key=None):
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.clear_state()
                raise TunnelError(f'Tunnel client exited during startup (status {proc.returncode}).\n'
                                  + self.log_tail(redact=key))
            if self.readiness()['readyz'] == 200:
                return True
            time.sleep(0.25)
        return False

    def readiness(self):
        try:
            base = self.health_file.read_text().strip().rstrip('/')
        except OSError:
            return {'healthz': None, 'readyz': None}
        return {'healthz': loopback_get(base + '/healthz'), 'readyz': loopback_get(base + '/readyz')}

    def stop(self):
        with self.locked():
            state = self.read_state()
            if not state or not self.alive(state):
                self.clear_state()
                return False
            pid = state['pid']
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGTERM)  # client and its MCP server child
            deadline = time.monotonic() + self.stop_timeout
            while time.monotonic() < deadline and self._still(pid, state):
                time.sleep(0.1)
            if self._still(pid, state):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGKILL)
            self.clear_state()
            return True

    def _still(self, pid, state):
        proc = getattr(self, '_proc', None)
        if proc is not None and proc.pid == pid:
            proc.poll()  # reap our own child so it is not left as a zombie
        return self.alive(state)

    def status(self):
        result = {'running': False, 'ready': False, 'pid': None, 'mode': None}
        try:
            tunnel_id = load_tunnel_id(self.root)
        except (OSError, ValueError, TypeError):
            tunnel_id = None
        state = self.read_state()
        if state and self.alive(state):
            probe = self.readiness()
            result.update(running=True, pid=state['pid'], mode=state['mode'],
                          uptime_seconds=int(time.time() - state.get('started_at', time.time())),
                          healthz=probe['healthz'], readyz=probe['readyz'],
                          ready=probe['readyz'] == 200)
        elif state:
            result['stale_state'] = True
        try:
            result['key_in_keychain'] = bool(tunnel_id and self.keychain.get(tunnel_id))
        except TunnelError:
            result['key_in_keychain'] = None
        result['tunnel_configured'] = tunnel_id is not None
        result['gui_session'] = self.is_gui()
        result['anki_reachable'] = anki_reachable()
        exclude = {state['pid']} if result['running'] else set()
        result['other_clients'] = [pid for pid, _ in self.other_clients(tunnel_id, exclude)] if tunnel_id else []
        return result


def anki_reachable():
    try:
        from anki_mcp import anki
        anki('version')
        return True
    except Exception:
        return False


def describe(status):
    if status['running']:
        state = 'ready' if status['ready'] else f"running, not ready yet (readyz={status['readyz']})"
        lines = [f"Tunnel: {state} — pid {status['pid']}, mode {status['mode']}, "
                 f"up {status['uptime_seconds']}s"]
    else:
        lines = ['Tunnel: stopped' + (' (stale record from an earlier run)' if status.get('stale_state') else '')]
    yes = {True: 'yes', False: 'no', None: 'unknown'}
    lines += [f"Runtime key in Keychain: {yes[status['key_in_keychain']]}",
              f"Anki reachable: {yes[status['anki_reachable']]}",
              f"Desktop session for approval dialogs: {yes[status['gui_session']]}"]
    if status['other_clients']:
        lines.append(f"Warning: other tunnel clients for this tunnel: {status['other_clients']}")
    if status['running'] and status['mode'] == 'additions':
        lines.append('Each card addition still requires clicking "Add card" on this Mac.')
    return '\n'.join(lines)


APPLESCRIPT = r'''
on ctl(args)
	set rootFile to (POSIX path of (path to me)) & "Contents/Resources/project-root.txt"
	set projectRoot to read (POSIX file rootFile) as «class utf8»
	set cmd to quoted form of (projectRoot & "/.venv/bin/python") & " " & quoted form of (projectRoot & "/tunnelctl.py") & " " & args & " 2>&1 || true"
	return do shell script cmd
end ctl

on run
	set choices to {"Status", "Start read-only", "Start with card additions", "Stop"}
	set picked to choose from list choices with title "Anki Tunnel" with prompt ctl("status") default items {"Status"}
	if picked is false then return
	set picked to item 1 of picked
	if picked is "Start read-only" then
		set out to ctl("start")
	else if picked is "Start with card additions" then
		display dialog "Allow the AI to request card additions?" & return & return & "Every card still opens a confirmation on this Mac; nothing is added unless you click Add card." with title "Anki Tunnel" buttons {"Cancel", "Start"} default button "Cancel" cancel button "Cancel"
		set out to ctl("start --enable-writes")
	else if picked is "Stop" then
		set out to ctl("stop")
	else
		set out to ctl("status")
	end if
	display dialog out with title "Anki Tunnel" buttons {"OK"} default button "OK"
end run
'''


def install_app(root, dest):
    dest.mkdir(parents=True, exist_ok=True)
    app = dest / APP_NAME
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / 'tunnel.applescript'
        script.write_text(APPLESCRIPT, encoding='utf-8')
        result = subprocess.run(['/usr/bin/osacompile', '-o', str(app), str(script)],
                                capture_output=True, text=True)
    if result.returncode:
        raise TunnelError(f'osacompile failed: {result.stderr.strip()}')
    # The path lives in a UTF-8 resource, not in AppleScript source, so any
    # folder name (spaces, quotes, Unicode) works without escaping.
    (app / 'Contents/Resources/project-root.txt').write_text(str(root), encoding='utf-8')
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    key = sub.add_parser('key', help='manage the runtime key in the macOS Keychain')
    key.add_argument('action', choices=['set', 'delete', 'status'])
    start = sub.add_parser('start', help='start in the background and wait for readiness')
    start.add_argument('--enable-writes', action='store_true',
                       help='expose add_card (each addition still needs the Mac dialog)')
    sub.add_parser('stop', help='stop the managed tunnel client')
    sub.add_parser('restart', help='restart in the current mode')
    status = sub.add_parser('status', help='show running state and prerequisites')
    status.add_argument('--json', action='store_true')
    status.add_argument('--check', action='store_true',
                        help='exit 0 ready, 1 stopped, 2 running but not ready')
    logs = sub.add_parser('logs', help='show the end of the client log')
    logs.add_argument('-n', type=int, default=40)
    app = sub.add_parser('install-app', help='build the one-click Anki Tunnel applet')
    app.add_argument('--dest', type=Path, help='folder for the applet (default: build/)')
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent
    tunnel = Tunnel(root)
    interactive = sys.stdin.isatty()
    try:
        if args.command == 'key':
            tunnel_id = load_tunnel_id(root)
            if args.action == 'set':
                if not interactive:
                    raise TunnelError('Run `key set` in your own interactive Terminal')
                tunnel.keychain.set(tunnel_id, getpass.getpass(
                    'Paste tunnel runtime API key (hidden): ').strip())
                print('Saved in the login Keychain (service anki-mcp-tunnel).')
            elif args.action == 'delete':
                print('Deleted.' if tunnel.keychain.delete(tunnel_id) else 'No key was stored.')
            else:
                print('Stored.' if tunnel.keychain.get(tunnel_id) else 'Not stored.')
        elif args.command in ('start', 'restart'):
            writes = args.enable_writes if args.command == 'start' else False
            if args.command == 'restart':
                previous = tunnel.running()
                writes = bool(previous and previous['mode'] == 'additions')
                tunnel.stop()
            result = tunnel.start(writes, interactive)
            if result['ready']:
                print(f"Tunnel ready (pid {result['pid']}, {result['mode']}).")
            else:
                print(f"Tunnel started (pid {result['pid']}, {result['mode']}) but not ready "
                      f"after {tunnel.ready_timeout}s; check `tunnelctl.py status` or `logs`.")
                return 2
            if writes:
                print('Each card addition still requires clicking "Add card" on this Mac.')
        elif args.command == 'stop':
            print('Tunnel stopped.' if tunnel.stop() else 'Tunnel was not running.')
        elif args.command == 'status':
            result = tunnel.status()
            print(json.dumps(result, indent=2) if args.json else describe(result))
            if args.check:
                return 0 if result['ready'] else 2 if result['running'] else 1
        elif args.command == 'logs':
            print(tunnel.log_tail(args.n))
        elif args.command == 'install-app':
            path = install_app(root, args.dest or root / 'build')
            print(f'Built {path}\nDrag it to the Dock or Applications for one-click use.')
    except (TunnelError, ValueError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

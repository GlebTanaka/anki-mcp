"""Start a foreground Anki tunnel with a hidden runtime-key prompt.

For background Start/Stop with the key in the macOS Keychain, use tunnelctl.py.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

KEY_ENV = 'CONTROL_PLANE_API_KEY'


def check_tunnel_id(tunnel_id):
    if not isinstance(tunnel_id, str) or not re.fullmatch(r'tunnel_[a-zA-Z0-9]+', tunnel_id):
        raise ValueError('Set a valid tunnel_id in tunnel.local.json')


def load_tunnel_id(root):
    data = json.loads((root / 'tunnel.local.json').read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'tunnel_id'}:
        raise ValueError('Tunnel settings accept only tunnel_id')
    check_tunnel_id(data['tunnel_id'])
    return data['tunnel_id']


def make_profile(root, tunnel_id, enable_writes=False):
    check_tunnel_id(tunnel_id)
    # The tunnel client needs the runtime key; the MCP server, and the approval
    # dialog it spawns, must not inherit it.
    command = ['/usr/bin/env', '-u', KEY_ENV,
               str(root / '.venv/bin/python'), str(root / 'anki_mcp.py')]
    if enable_writes:
        command.append('--enable-writes')
    return {
        'config_version': 1,
        'control_plane': {'base_url': 'https://api.openai.com',
                          'tunnel_id': tunnel_id, 'api_key': f'env:{KEY_ENV}'},
        'health': {'listen_addr': '127.0.0.1:0'},
        'admin_ui': {'open_browser': False},
        'log': {'level': 'info', 'format': 'json'},
        'mcp': {'commands': [{'channel': 'main', 'command': shlex.join(command)}]},
    }


def write_profile(path, profile):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    # JSON is valid YAML; no extra YAML dependency and no embedded credential.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(json.dumps(profile, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-writes', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    client = root / '.tools/tunnel-client/tunnel-client'
    from tunnelctl import Tunnel, TunnelError
    try:
        from anki_mcp import load_settings
        load_settings(root / 'config.local.json')
        tunnel_id = load_tunnel_id(root)
        profile = make_profile(root, tunnel_id, args.enable_writes)
        if not client.is_file() or not (root / '.venv/bin/python').is_file():
            raise ValueError('Install the tunnel client and create .venv first')
        if not sys.stdin.isatty():
            raise ValueError('Run in your own interactive Terminal')
        tunnel = Tunnel(root)
        with tunnel.locked(tunnel_id):
            tunnel.ensure_no_other_client(tunnel_id)
            # Held across exec: the lock lives exactly as long as this client.
            lock_fd = tunnel.acquire_client_lock(tunnel_id, 'start_tunnel.py')
    except (OSError, ValueError, TypeError, TunnelError) as exc:
        parser.error(str(exc))
    config = root / 'tunnel-profiles' / 'generated.yaml'
    write_profile(config, profile)
    key = getpass.getpass('Paste tunnel runtime API key (hidden): ').strip()
    if not key:
        parser.error('No key supplied; nothing started')
    env = dict(os.environ, **{KEY_ENV: key})
    del key
    result = subprocess.run([str(client), 'doctor', '--config', str(config), '--explain'], env=env)
    if result.returncode:
        raise SystemExit('Preflight failed; tunnel not started. Do not share the key.')
    mode = 'confirmation-gated' if args.enable_writes else 'read-only'
    print(f'Starting {mode} tunnel. Keep Terminal open; Ctrl+C stops it.', flush=True)
    os.set_inheritable(lock_fd, True)
    os.execve(str(client), [str(client), 'run', '--config', str(config),
                            '--health.url-file', str(root / '.tunnel-health.url')], env)


if __name__ == '__main__':
    main()

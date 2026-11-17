"""Start a foreground Anki tunnel with a hidden runtime-key prompt."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def make_profile(root, tunnel_id, enable_writes=False):
    if not isinstance(tunnel_id, str) or not re.fullmatch(r'tunnel_[a-zA-Z0-9]+', tunnel_id):
        raise ValueError('Set a valid tunnel_id in tunnel.local.json')
    command = [str(root / '.venv/bin/python'), str(root / 'anki_mcp.py')]
    if enable_writes:
        command.append('--enable-writes')
    return {
        'config_version': 1,
        'control_plane': {'base_url': 'https://api.openai.com',
                          'tunnel_id': tunnel_id, 'api_key': 'env:CONTROL_PLANE_API_KEY'},
        'health': {'listen_addr': '127.0.0.1:0'},
        'admin_ui': {'open_browser': False},
        'log': {'level': 'info', 'format': 'json'},
        'mcp': {'commands': [{'channel': 'main', 'command': shlex.join(command)}]},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-writes', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    client = root / '.tools/tunnel-client/tunnel-client'
    try:
        from anki_mcp import load_settings
        load_settings(root / 'config.local.json')
        data = json.loads((root / 'tunnel.local.json').read_text())
        if not isinstance(data, dict) or set(data) != {'tunnel_id'}:
            raise ValueError('Tunnel settings accept only tunnel_id')
        profile = make_profile(root, data['tunnel_id'], args.enable_writes)
        if not client.is_file() or not (root / '.venv/bin/python').is_file():
            raise ValueError('Install the tunnel client and create .venv first')
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    if not sys.stdin.isatty():
        parser.error('Run in your own interactive Terminal')
    folder = root / 'tunnel-profiles'
    folder.mkdir(mode=0o700, exist_ok=True)
    config = folder / 'generated.yaml'
    # JSON is valid YAML; no extra YAML dependency and no embedded credential.
    config.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    config.chmod(0o600)
    key = getpass.getpass('Paste tunnel runtime API key (hidden): ').strip()
    if not key:
        parser.error('No key supplied; nothing started')
    env = dict(os.environ, CONTROL_PLANE_API_KEY=key)
    del key
    result = subprocess.run([str(client), 'doctor', '--config', str(config), '--explain'], env=env)
    if result.returncode:
        raise SystemExit('Preflight failed; tunnel not started. Do not share the key.')
    mode = 'confirmation-gated' if args.enable_writes else 'read-only'
    print(f'Starting {mode} tunnel. Keep Terminal open; Ctrl+C stops it.', flush=True)
    os.execve(str(client), [str(client), 'run', '--config', str(config),
                            '--health.url-file', str(root / '.tunnel-health.url')], env)


if __name__ == '__main__':
    main()

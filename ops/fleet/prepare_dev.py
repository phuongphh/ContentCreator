"""Prepare (never start) the isolated, publishing-disabled approval service."""
import argparse
import importlib.util
import json
import os
from pathlib import Path


def prepare(artifact, state, output):
    artifact, state, output = map(lambda p: Path(p).absolute(), (artifact, state, output))
    if not (artifact / 'fleet-artifact.json').is_file():
        raise ValueError('A SHA-pinned staged artifact is required')
    if output.exists() or output.is_symlink():
        raise ValueError('Use a new private compose file')
    spec = importlib.util.spec_from_file_location('dev_mode', artifact / 'content-pipeline/runtime_mode.py')
    mode = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mode)
    mode.initialize_dev_state(state)
    document = {
        'name': 'fleet-content-dev',
        'services': {'approval': {
            'image': 'fleet-content-dev:local',
            'build': {'context': str(artifact), 'dockerfile': 'ops/fleet/Dockerfile.dev'},
            'command': ['python', '/app/ops/fleet/content_dev.py'],
            'user': f'{os.getuid()}:{os.getgid()}',
            'environment': {'CONTENT_RUNTIME_MODE': 'development', 'CONTENT_STATE_DIR': '/state',
                            'HOME': '/tmp', 'PYTHONPATH': '/app/content-pipeline'},
            'volumes': [f'{artifact}:/app:ro', f'{state}:/state'],
            'ports': ['127.0.0.1:18866:8501'],
            'networks': ['isolated'], 'read_only': True, 'tmpfs': ['/tmp'],
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'mem_limit': '768m', 'cpus': '1.0', 'pids_limit': 128,
            'restart': 'unless-stopped',
            'healthcheck': {'test': ['CMD', 'python', '-c',
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"],
                'interval': '15s', 'timeout': '5s', 'retries': 4},
        }},
        'networks': {'isolated': {'internal': True}},
    }
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with output.open('x') as handle:
        json.dump(document, handle, indent=2)
    output.chmod(0o600)
    return {'compose': str(output), 'url': 'http://127.0.0.1:18866', 'started': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.artifact, args.state, args.output)))

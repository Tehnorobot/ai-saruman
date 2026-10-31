"""Offline parser/config check on the pinned CLI; never starts a model turn."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from common.codex_settings import codex_agent_overrides

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    workspace = root/'workspace'
    roles = workspace/'.codex'/'agents'
    roles.mkdir(parents=True)
    for role in ('architect', 'implementer', 'reviewer', 'tester'):
        (roles/(role+'.toml')).write_text('developer_instructions = "Synthetic read-only configuration check"\n')
    options = codex_agent_overrides(workspace)
    (root/'empty-codex-home').mkdir()
    environment = {**os.environ, 'HOME': str(root), 'CODEX_HOME': str(root/'empty-codex-home')}
    version = subprocess.run(['codex', '--version'], capture_output=True, text=True, env=environment, timeout=20)
    result = subprocess.run(['codex', 'features', 'list', *options], capture_output=True,
                            text=True, env=environment, cwd=workspace, timeout=30)
    print(json.dumps({'version': version.stdout.strip(), 'exit_code': result.returncode,
                      'multi_agent': [line for line in result.stdout.splitlines() if 'multi_agent' in line],
                      'stderr': result.stderr[-3000:]}, ensure_ascii=False))
    if result.returncode:
        raise SystemExit(result.returncode)

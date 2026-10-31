"""Validated per-job Codex model / reasoning selection.

Model access and supported effort tiers are determined by the connected Codex
account and the model catalog, not this dashboard. An unsupported combination
must fail visibly; there is no implicit fallback or extra API billing.
"""
from __future__ import annotations
import re
import json
from pathlib import Path

REASONING_EFFORTS = ('default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
CODEX_MODELS = ('gpt-6.1-sol','gpt-6-astra','gpt-6-sol','gpt-6-luna',
                'gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna')
CODEX_MODEL_EFFORTS = {model: ('default','low','medium','high','xhigh','max') +
    (() if model in ('gpt-6-luna','gpt-5.6-luna') else ('ultra',)) for model in CODEX_MODELS}
_MODEL_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}\Z')


def validate_model(value: str | None) -> str:
    model = (value or '').strip()
    if model and not _MODEL_PATTERN.fullmatch(model):
        raise ValueError('Invalid model identifier; use a model ID, not CLI arguments')
    return model


def validate_reasoning(value: str | None) -> str:
    effort = (value or 'default').strip().lower()
    if effort not in REASONING_EFFORTS:
        raise ValueError('Unsupported reasoning setting')
    return effort


def codex_cli_overrides(model: str | None, reasoning: str | None) -> list[str]:
    model = validate_model(model)
    effort = validate_reasoning(reasoning)
    args = []
    if model:
        args.extend(['--model', model])
    if effort != 'default':
        args.extend(['-c', 'model_reasoning_effort="' + effort + '"'])
    return args


def codex_agent_overrides(workspace: Path) -> list[str]:
    """Load generated roles explicitly, even when project config is untrusted."""
    args = ['-c', 'features.multi_agent=true', '-c', 'agents.max_concurrent_threads_per_session=3',
            '-c', 'agents.max_depth=1']
    for role in ('architect', 'implementer', 'reviewer', 'tester'):
        config = workspace/'.codex'/'agents'/(role+'.toml')
        if config.is_file():
            if config.is_symlink() or not config.resolve().is_relative_to(workspace.resolve()):
                raise ValueError('Agent role config escapes workspace')
            args.extend(['-c', f'agents.{role}.config_file='+json.dumps(str(config))])
            args.extend(['-c', f'agents.{role}.description='+json.dumps(role+' task from AGENTS.md')])
    return args

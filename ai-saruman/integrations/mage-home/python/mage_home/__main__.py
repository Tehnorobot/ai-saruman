"""Java subprocess boundary, exactly one UTF-8 object in and out."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import monotonic

from .blocks import MageError, require, xyz
from .blueprint import compile_plan, context_anchor, obj, save_project, text, validate_blueprint
from .design_cache import read_design, remember_design
from .design_history import DesignHistory, digest
from .expert import expert
from .interior import furnish, wants_interior
from .providers import MAX_JSON, OpenAIProvider, decode, load_json, load_profile


def check_replan(blueprint, recovery):
    baseline = recovery['baseline']
    center = xyz(recovery['center'])
    radius = recovery['radius']
    require(type(radius) is int and 1 <= radius <= 12, 'invalid repair radius', 'invalid_request')
    require(blueprint['origin'] == baseline['origin'], 'keep the original blueprint origin', 'replan_scope')
    require(set(baseline['requirements']['mandatory']) <= set(blueprint['requirements']['mandatory']),
            'keep all previously validated mandatory requirements', 'replan_scope')
    if blueprint.get('paintings', []) != baseline.get('paintings', []):
        blueprint['paintings'] = baseline.get('paintings', [])
    old = {xyz(c): (c['block'], c['state']) for c in baseline['cells']}
    new = {xyz(c): (c['block'], c['state']) for c in blueprint['cells']}
    old_air = {xyz(c) for c in baseline['requiredAir']}
    new_air = {xyz(c) for c in blueprint['requiredAir']}
    changed = {p for p in old.keys() | new.keys() if old.get(p) != new.get(p)} | (old_air ^ new_air)
    require(changed, 'the repair must change the local plan', 'replan_scope')
    outside = [p for p in sorted(changed) if sum(abs(p[i] - center[i]) for i in range(3)) > radius]
    require(not outside, f'keep the original block and air targets outside the repair region: {outside[:8]}', 'replan_scope')
    abandoned = sorted(old.keys() - new.keys() - new_air)
    require(not abandoned, f'removed goals must be cleared as required air: {abandoned[:8]}', 'replan_scope')
    blueprint['revision'] = baseline['revision'] + 1


def compile_design(provider, description, context, project_id, *, history=None, recovery=None, feedback=None):
    if not isinstance(provider, OpenAIProvider):
        blueprint = furnish(compile_plan(provider.design(description, context), description, context, project_id), context)
        if recovery:
            check_replan(blueprint, recovery)
            validate_blueprint(blueprint, context)
        return blueprint

    started = monotonic()
    history = history or DesignHistory(None, project_id, '')
    last = history.recent[-1] if history.recent else None
    strategy = 'repair'
    stagnant = 0
    approved = history.approved or {}
    previous = last['plan'] if last else approved.get('plan') if recovery and approved.get('blueprint') == recovery['baseline'] else None
    failures = [row['errors'] for row in history.recent]
    if feedback:
        previous = approved.get('plan') or previous or {}
        failures.append([feedback])
        history.add('minecraft_rejected', errors=[feedback], plan=previous)
    while True:
        remaining = 0
        if provider.deadline:
            remaining = provider.deadline - int((monotonic() - started) * 1000)
            require(remaining > 0, 'design exceeded the configured deadline', 'provider_timeout')
        revision = None
        if previous is not None or recovery:
            hint = {k: v for k, v in recovery.items() if k != 'baseline'} if recovery else None
            revision = {'previousPlan': previous, 'strategy': strategy, 'recovery': hint,
                        'compilerError': failures[-1][0] if failures else None,
                        'compilerErrors': failures[-1] if failures else [],
                        'earlierCompilerErrors': [item for errors in failures[-9:-1] for item in errors][:40]}
        try:
            plan = provider.design(description, context, revision=revision, deadline=remaining)
        except MageError as error:
            history.add('provider_error', errors=[{'code': error.code, 'detail': str(error)[:512]}], strategy=strategy)
            if error.code in ('provider_output_limit', 'provider_format') and strategy != 'simplify':
                strategy = 'simplify'
                previous = previous or {}
                continue
            raise
        try:
            pitched_default = not recovery or approved.get('roofPolicy') == 'pitched'
            blueprint = furnish(compile_plan(plan, description, context, project_id,
                                            pitched_default=pitched_default), context)
            if recovery:
                check_replan(blueprint, recovery)
                validate_blueprint(blueprint, context)
        except MageError as error:
            issues = getattr(error, 'diagnostics', [{'code': error.code, 'detail': str(error)[:512]}])
            fingerprint = digest(plan)
            repeated = any(row['fingerprint'] == fingerprint or row['errors'] == issues for row in history.recent)
            history.add('rejected', plan=plan, errors=issues, fingerprint=fingerprint, strategy=strategy)
            stagnant = stagnant + 1 if repeated else 0
            if stagnant >= 2:
                if strategy == 'simplify':
                    history.add('waiting', reason='design_stalled', errors=issues)
                    raise MageError('stalled', str(error)) from error
                strategy, stagnant = 'simplify', 0
            failures = (failures + [issues])[-12:]
            previous = plan
        else:
            history.add('confirmed', plan=plan, blueprint=blueprint, strategy=strategy,
                        roofPolicy='pitched' if pitched_default else 'legacy')
            return blueprint


def invoke(operation, profile_path, value):
    obj(value,'CLI input')
    config = obj(load_json(profile_path), 'profile')
    provider, archive = load_profile(profile_path, config=config)
    if operation == 'expert':
        return {'ok':True,'proposal':expert(value,provider)}
    required = {'description', 'context', 'projectId'}
    require(required <= value.keys() <= required | {'runId', 'recovery', 'feedback'},
            'design requires description, context, and projectId','invalid_request')
    description = text(value.get('description'),'description')
    project_id = text(value.get('projectId'),'projectId',max_len=256)
    context = value.get('context')
    context_anchor(context)
    recovery = obj(value['recovery'], 'recovery') if 'recovery' in value else None
    feedback = obj(value['feedback'], 'feedback') if 'feedback' in value else None
    history_setting = config.get('designJournalDirectory')
    journal = Path(profile_path).parent / text(history_setting, 'designJournalDirectory', max_len=4096) if history_setting else None
    history = DesignHistory(journal, project_id, digest([description, context, recovery]), value.get('runId'))
    cache_setting = config.get('designCacheDirectory')
    cache = Path(profile_path).parent / text(cache_setting, 'designCacheDirectory', max_len=4096) if cache_setting is not None else None
    use_cache = cache is not None and not (recovery or feedback or wants_interior(description))
    blueprint = read_design(cache, description, context, project_id) if use_cache else None
    if blueprint is None:
        blueprint = compile_design(provider, description, context, project_id, history=history, recovery=recovery, feedback=feedback)
        if use_cache:
            try:
                remember_design(cache, description, context, blueprint)
            except OSError:
                pass  # A full or read-only cache must not discard a validated design.
    else:
        history.add('confirmed', blueprint=blueprint, reason='validated_cache')
    if archive is not None:
        save_project(archive,blueprint)
    return {'ok':True,'blueprint':blueprint}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m mage_home')
    parser.add_argument('operation',choices=('design','expert'))
    parser.add_argument('--profile',required=True)
    args = parser.parse_args(argv)
    try:
        data = sys.stdin.buffer.read(MAX_JSON+1)
        require(len(data)<=MAX_JSON,'CLI request exceeds size limit','invalid_request')
        result = invoke(args.operation,args.profile,decode(data))
    except MageError as err:
        code = err.code
        if args.operation=='expert' and code in ('invalid_blueprint','invalid_coordinates','invalid_block_state'):
            code = 'invalid_expert_data'
        result = {'ok':False,'error':{'code':code,'message':str(err)[:4096]}}
    except OSError:
        result = {'ok':False,'error':{'code':'io_error','message':'could not access standalone project data'}}
    except (TypeError,ValueError,KeyError,RecursionError,OverflowError):
        result = {'ok':False,'error':{'code':'invalid_data','message':'request, profile or provider data has an invalid type'}}
    payload = json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n'
    try:
        encoded = payload.encode('utf-8')
    except UnicodeError:
        encoded = b'{"ok":false,"error":{"code":"invalid_json","message":"invalid Unicode scalar"}}\n'
    sys.stdout.buffer.write(encoded)
    return 0


if __name__=='__main__':
    raise SystemExit(main())

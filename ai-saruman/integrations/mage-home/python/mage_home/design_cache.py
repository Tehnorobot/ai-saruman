"""Reuse a compiled design only for the same description and site."""
from hashlib import sha256
import json

from .blueprint import save_project, validate_blueprint
from .providers import load_json


def cache_id(description, context):
    world = context['world']
    scope = {
        'description': description,
        'world': world.get('worldUid') or world.get('server'),
        'dimension': world['dimension'],
        'anchor': context['anchor'],
        'capabilities': context.get('capabilities', {}),
    }
    payload = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return 'design-cache/3:' + sha256(payload.encode('utf-8')).hexdigest()


def read_design(directory, description, context, project_id):
    key = cache_id(description, context)
    path = directory / (sha256(key.encode()).hexdigest() + '.json')
    try:
        blueprint = load_json(path)
        if (not isinstance(blueprint, dict) or blueprint.get('projectId') != key
                or blueprint.get('description') != description):
            return None
        blueprint = {**blueprint, 'projectId': project_id}
        validate_blueprint(blueprint, context)
        return blueprint
    except (OSError, KeyError, TypeError, ValueError):
        return None


def remember_design(directory, description, context, blueprint):
    save_project(directory, {**blueprint, 'projectId': cache_id(description, context)})

"""Trusted planner shape checks; controller semantics and evidence remain separate."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from itertools import islice
import json
from pathlib import Path

from jsonschema import Draft202012Validator


_SCHEMA_PATH = Path(__file__).resolve().parent.parent / 'schemas' / 'planner_output.schema.json'
MAX_ERRORS = 8
MAX_ERROR_CHARS = 1200


class PlannerStructureError(ValueError):
    """A bounded, public shape error, without echoing untrusted planner values."""

    def __init__(self, paths):
        self.paths = tuple(paths[:MAX_ERRORS])
        super().__init__(('Invalid planner output structure: ' + '; '.join(self.paths))[:MAX_ERROR_CHARS])


@lru_cache(maxsize=1)
def _trusted_schema():
    # Never consult the job workspace, environment variables, or a provider schema.
    schema = json.loads(_SCHEMA_PATH.read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return schema


def load_planner_schema():
    """Return an independent copy of the image-owned canonical JSON Schema."""
    return deepcopy(_trusted_schema())


PLANNER_EXAMPLES = ('implement','repair','diagnose','review_coverage','bundle','manual',
                    'verify','repair_acceptance','repair_verification')


def load_planner_example(name):
    if name not in PLANNER_EXAMPLES:
        raise ValueError('Unknown planner format example')
    example=json.loads((_SCHEMA_PATH.parent/'examples'/(name+'.json')).read_text(encoding='utf-8'))
    validate_planner_structure(example)
    return example


def _error_path(error):
    path = '$'
    for part in error.absolute_path:
        if isinstance(part, int):
            path += '[' + str(part) + ']'
        elif isinstance(part, str) and part.isidentifier() and len(part) <= 64:
            path += '.' + part
        else:
            path += '[field]'
        if len(path) > 180:
            path = path[:180] + '...'
            break
    # Validator names are trusted, unlike jsonschema's messages (which quote input).
    suffix=' (' + str(error.validator)[:32] + ')'
    if error.validator=='additionalProperties' and isinstance(error.instance,dict):
        known_root=('hypotheses','next_steps','manual_reviews','coverage_reviews',
                    'additional_actions','bundle_reason','verification_limit')
        misplaced=[key for key in known_root if key in error.instance and key not in error.schema.get('properties',{})]
        if misplaced:suffix+='; root fields misplaced here: '+', '.join(misplaced)
    return path+suffix


def validate_planner_structure(value):
    """Return the unchanged object or raise PlannerStructureError.

    This accepts parsed JSON only. It does not repair JSON, normalize plans, check
    task scope, prove citations, approve coverage, or establish a passing result.
    """
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 100000 or depth > 32:
            raise PlannerStructureError(['$ (complexity limit)'])
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    validator = Draft202012Validator(_trusted_schema())
    try:
        paths = [_error_path(error) for error in islice(validator.iter_errors(value), MAX_ERRORS)]
    except RecursionError:
        raise PlannerStructureError(['$ (complexity limit)']) from None
    if paths:
        raise PlannerStructureError(paths)
    return value

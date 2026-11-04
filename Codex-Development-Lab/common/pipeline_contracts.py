"""Image-owned v1 contracts for the semantic development pipeline.

Shape validation is deterministic. It never repairs JSON, certifies a feature,
or accepts a workspace-owned schema. Semantic graph checks live in task_graph.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


VERSION = 'v1'
SCHEMA_ROOT = Path(__file__).resolve().parents[1] / 'schemas' / 'pipeline'
NAMES = (
    'ProjectInput', 'ProjectSnapshot', 'DecompositionInput', 'RequirementsCatalog',
    'TaskGraph', 'PlannerInput', 'PlannerDecision', 'ValidationRequest',
    'ValidationResult', 'CoderAssignment', 'CoderReport', 'ScenarioManifest',
    'ReviewRequest', 'ReviewResult', 'ReviewResponse', 'VerificationRequest',
    'VerificationResult', 'MemoryWrite', 'MemoryRecord', 'MemoryQuery',
    'MemoryResult', 'GatewayRequest', 'GatewayResult', 'ProjectStatus', 'CatalogPatch', 'PreparationHandoff', 'SubmissionPatch',
    'AgentPlan','AgentReview','AgentReport','AgentGraph','AgentCatalog','AgentCatalogPatch','AgentScenarios',
)


class ContractError(ValueError):
    """Public diagnostics contain trusted constraints, never submitted values."""

    def __init__(self, name, issues):
        self.schema_name = name
        self.issues = deepcopy(list(issues))
        self.paths = tuple(issue['path'] for issue in self.issues)
        super().__init__('Invalid ' + name + ': ' + '; '.join(
            issue['path'] + ' (' + issue['code'] + '): ' + issue['expected']
            for issue in self.issues))


def contract_names():
    return NAMES


def _name(name):
    if name not in NAMES:
        raise ValueError('Unknown pipeline contract')
    return name


@lru_cache(maxsize=len(NAMES))
def _schema(name):
    path = SCHEMA_ROOT / (_name(name) + '.schema.json')
    value = json.loads(path.read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(value)
    return value


def load_schema(name):
    return deepcopy(_schema(name))


def submission_schema(name):
    """Portable generation grammar; the complete contract is still validated.

    Provider decoders accept different JSON Schema constraint subsets. Keep the
    exact object/array/types/enums here; filesystem patterns and numeric/string
    constraints remain enforced by the trusted full validator after generation.
    """
    def project(node):
        result={}
        for key in ('type','description','required','additionalProperties','$ref','enum'):
            if key in node:result[key]=deepcopy(node[key])
        if 'const' in node:
            value=node['const'];result['enum']=[value]
            result.setdefault('type','boolean' if isinstance(value,bool) else 'integer' if isinstance(value,int) else 'string')
        for key in ('properties','$defs'):
            if key in node:result[key]={name:project(value) for name,value in node[key].items()}
        if 'properties' in result:
            # Strict generation requires EVERY property, including fields that
            # remain optional in stored historical contracts. An empty array
            # is an explicit new answer, not a migration of an old record.
            result['required']=list(result['properties'])
        if 'items' in node:result['items']=project(node['items'])
        for key in ('anyOf','oneOf','allOf'):
            if key in node:result[key]=[project(value) for value in node[key]]
        return result
    schema=project(load_schema(name))
    check_submission_schema(schema)
    return schema


def check_submission_schema(schema):
    """Preflight provider structural rules beyond ordinary JSON Schema.

    This checks the strict subset we generate; it is not a remote API test.
    Configuration errors must fail locally, before a model request is launched.
    """
    Draft202012Validator.check_schema(schema)
    if schema.get('type')!='object':raise ValueError('Generation schema root must be an object')
    def check(node,path='$'):
        if not isinstance(node,dict):return
        if node.get('type')=='object' or 'properties' in node:
            properties=node.get('properties',{})
            required=node.get('required',[])
            if set(required)!=set(properties) or len(required)!=len(properties):
                raise ValueError('Invalid generation schema at '+path+': required must include every property exactly once')
            if node.get('additionalProperties') is not False:
                raise ValueError('Invalid generation schema at '+path+': additionalProperties must be false')
        if any(key in node for key in ('oneOf','allOf')):
            raise ValueError('Invalid generation schema at '+path+': unsupported composition; use the portable anyOf contract')
        for key in ('properties','$defs'):
            for name,value in node.get(key,{}).items():check(value,path+'.'+key+'.'+name)
        if 'items' in node:check(node['items'],path+'.items')
        for n,value in enumerate(node.get('anyOf',[])):check(value,path+'.anyOf['+str(n)+']')
    check(schema)
    return schema


def load_submission_schema(name):
    """Read only the trusted image artifact and reject stale generated files."""
    path=SCHEMA_ROOT/(_name(name)+'.submission.schema.json')
    value=json.loads(path.read_text(encoding='utf-8'))
    check_submission_schema(value)
    if value!=submission_schema(name):
        raise ValueError('Stale image-owned generation schema for '+name+'; regenerate schemas and rebuild the executor image')
    return value


def load_example(name):
    _name(name)
    value = json.loads((SCHEMA_ROOT / 'examples' / (name + '.json')).read_text(encoding='utf-8'))
    return validate(name, value)


def format_guide(name):
    """Small, trusted first-turn material; complete schema remains on demand."""
    schema = submission_schema(name)
    def describe(node):
        node = _resolve(node, schema)
        result = {'type': _type_description(node, schema)}
        if node.get('type') == 'object':
            result['required'] = node.get('required', [])
            result['fields'] = {key: describe(value) for key, value in node.get('properties', {}).items()}
        elif node.get('type') == 'array':
            result['item'] = describe(node.get('items', {}))
        return result
    instruction=('Example values are fictional. Arrays stay arrays even for one item; empty arrays are []. '
                 'Use integer round/line numbers. Preserve your decisions while correcting types; no project reread for format repair.')
    if name in ('AgentScenarios','ScenarioManifest'):
        instruction+=(' execution.precondition_tests is optional. Use [] when no independent setup assertion is needed. '
            'A prerequisite must be listed in expected_tests and must not also be bound to a checkpoint with condition_ids '
            'in the same scenario. Keep product assertions and condition links; use a distinct genuine setup test when needed. '
            'A JAR metadata unit test does not prove that a game action or world observation occurred.')
    return {'shape': describe(schema), 'example': load_example(name), 'instruction':instruction}


def _typename(value):
    if value is None: return 'null'
    if isinstance(value, bool): return 'boolean'
    if isinstance(value, str): return 'string'
    if isinstance(value, int): return 'integer'
    if isinstance(value, float): return 'number'
    if isinstance(value, dict): return 'object'
    if isinstance(value, list): return 'array'
    return 'non-JSON value'


def _resolve(schema, root):
    while '$ref' in schema:
        value = root
        for key in schema['$ref'].split('/')[1:]:
            value = value[key.replace('~1', '/').replace('~0', '~')]
        schema = value
    return schema


def _type_description(schema, root):
    schema = _resolve(schema, root)
    if 'const' in schema:
        return 'constant ' + json.dumps(schema['const'], ensure_ascii=False)
    if 'enum' in schema:
        return 'one of ' + json.dumps(schema['enum'], ensure_ascii=False)
    if 'anyOf' in schema or 'oneOf' in schema:
        return ' | '.join(_type_description(s, root) for s in schema.get('anyOf', schema.get('oneOf', [])))
    kind = schema.get('type', 'JSON')
    if kind == 'array':
        return 'array<' + _type_description(schema.get('items', {}), root) + '>'
    return ' | '.join(kind) if isinstance(kind, list) else kind


def _safe_path(parts, root):
    schema = _resolve(root, root)
    result = '$'
    for part in parts:
        schema = _resolve(schema, root)
        if isinstance(part, int):
            result += '[' + str(part) + ']'
            schema = schema.get('items', {})
        elif part in schema.get('properties', {}):
            result += '.' + part
            schema = schema['properties'][part]
        else:
            result += '[field]'
            schema = {}
    return result


def _example_value(schema, root):
    schema = _resolve(schema, root)
    if 'const' in schema: return schema['const']
    if 'enum' in schema: return schema['enum'][0]
    if 'anyOf' in schema or 'oneOf' in schema:
        return _example_value(schema.get('anyOf', schema.get('oneOf'))[0], root)
    kind = schema.get('type')
    if isinstance(kind, list): kind = next((k for k in kind if k != 'null'), 'null')
    if kind == 'array': return [_example_value(schema.get('items', {}), root)]
    if kind == 'object':
        return {k: _example_value(schema['properties'][k], root) for k in schema.get('required', [])}
    if kind == 'integer': return schema.get('minimum', 0)
    if kind == 'number': return schema.get('minimum', 0)
    if kind == 'boolean': return True
    if kind == 'null': return None
    if schema.get('pattern') == '^[a-f0-9]{64}$': return 'a' * 64
    if schema.get('format') == 'date-time': return '2026-10-26T00:00:00Z'
    return 'value'


def _issue(error, root):
    schema = error.schema
    expected = _type_description(schema, root)
    code = error.validator
    if code == 'required':
        missing = [k for k in schema.get('required', []) if k not in error.instance]
        expected = 'required fields: ' + ', '.join(missing)
    elif code == 'additionalProperties':
        expected = 'only fields: ' + ', '.join(schema.get('properties', {}))
    elif code in ('minLength', 'maxLength', 'minimum', 'maximum', 'minItems', 'maxItems', 'pattern', 'format'):
        expected += '; ' + code + '=' + str(error.validator_value)
    return {
        'path': _safe_path(error.absolute_path, root),
        'code': code,
        'expected': expected,
        'received_type': _typename(error.instance),
        'example_json': json.dumps(_example_value(schema, root), ensure_ascii=False),
        'instruction': 'Correct this field to the stated schema. Preserve the intended result; do not infer missing requirements.',
    }


def validate(name, payload):
    """Return the unchanged parsed JSON, or ContractError with exact constraints."""
    schema = _schema(name)
    # Reject non-JSON/cyclic Python inputs before the recursive schema validator.
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ContractError(name, [{
            'path':'$', 'code':'json_value', 'expected':'finite, acyclic JSON data',
            'received_type':_typename(payload), 'example_json':'{}',
            'instruction':'Return JSON data only. Do not include NaN, Infinity or Python objects.',
        }]) from None
    try:
        errors = [_issue(error, schema) for error in Draft202012Validator(
            schema, format_checker=FormatChecker()).iter_errors(payload)]
    except RecursionError:
        raise ContractError(name, [{
            'path':'$', 'code':'json_nesting', 'expected':'JSON within parser nesting capacity',
            'received_type':_typename(payload), 'example_json':'{}',
            'instruction':'Use the supplied contract shape rather than recursive nested documents.',
        }]) from None
    if errors:
        raise ContractError(name, errors)
    return payload


def validate_submission(name,payload):
    """New model answers follow generation AND full safety constraints.

    validate() deliberately continues accepting legacy stored catalogs.
    """
    validate(name,payload)
    schema=submission_schema(name)
    errors=[_issue(error,schema) for error in Draft202012Validator(schema).iter_errors(payload)]
    if errors:raise ContractError(name,errors)
    return payload


def feedback(exc, name, attempt=1):
    """Adaptive correction material; no fixed correction count or raw-value echo."""
    _name(name)
    if isinstance(exc, ContractError):
        issues = deepcopy(exc.issues)
    else:
        issues = [{
            'path':'$', 'code':'json_syntax' if isinstance(exc, json.JSONDecodeError) else 'validation',
            'expected':'one complete ' + name + ' JSON object', 'received_type':'unparsed or rejected',
            'example_json':json.dumps(load_example(name), ensure_ascii=False),
            'instruction':'Use the schema and complete example. Do not wrap JSON in Markdown.',
        }]
    instructions = (
        'Correct the listed fields. The original attempt and any source edits are retained; validation does not establish success.',
        'Compare each rejected field with its expected type and example. Read the trusted schema; no project reread is needed for a format correction.',
        'Change the representation strategy: use the full valid example as a shape template, substitute your actual decisions, and submit one complete object. Do not repeat the same invalid representation.',
    )
    stage = min(max(int(attempt) - 1, 0), len(instructions) - 1)
    return {'contract':'ContractFeedback', 'version':VERSION, 'schema_name':name,
            'attempt':attempt, 'issues':issues, 'instruction':instructions[stage],
            'schema_path':'/opt/lab/schemas/pipeline/' + name + '.submission.schema.json',
            'example_path':'/opt/lab/schemas/pipeline/examples/' + name + '.json',
            'example':load_example(name)}

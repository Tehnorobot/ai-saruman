"""Read-only checks for the proposed documentation contracts; requires jsonschema."""
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'pipeline'
schema = json.loads((DOC/'contracts.schema.json').read_text(encoding='utf-8'))
examples = json.loads((DOC/'examples.json').read_text(encoding='utf-8'))
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema, format_checker=FormatChecker())
checked = 0
for name, example in examples.items():
    validator.validate(example)
    standalone = json.loads((DOC/'contracts'/f'{name}.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(standalone)
    local = Draft202012Validator(standalone, format_checker=FormatChecker())
    local.validate(example)
    bad = {**example, 'unexpected_field': 'must fail'}
    assert not local.is_valid(bad), name
    assert not local.is_valid({k:v for k,v in example.items() if k != 'contract'}), name
    checked += 1

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = []
        self.scripts = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag == 'a' and 'href' in attrs: self.links.append(attrs['href'])
        if tag == 'script' and attrs.get('src'): self.scripts.append(attrs['src'])

page = (DOC/'index.html').read_text(encoding='utf-8')
parser = Links()
parser.feed(page)
assert len(parser.ids) == len(set(parser.ids)), 'duplicate DOM IDs'
assert not parser.scripts, 'external scripts'
for target in parser.links:
    parts = urlsplit(target)
    assert not parts.scheme and not parts.netloc, target
    if parts.path:
        assert (DOC/unquote(parts.path)).is_file(), target
    elif parts.fragment:
        assert parts.fragment in parser.ids, target
assert '/*__DATA__*/' not in page
assert '<html lang="ru">' in page
assert 'prefers-reduced-motion' in page
assert 'fetch(' not in page

# Documentation examples must not confuse observed failure with the next attempt.
assert examples['VerificationResult']['scope']['round'] < examples['PlannerInput']['scope']['round']
assert examples['CoderReport']['base_source_revision'] != examples['CoderReport']['scope']['source_revision']
assert examples['CoderReport']['scope']['source_revision'] == examples['VerificationRequest']['scope']['source_revision']
assert examples['ReviewResult']['scope']['source_revision'] != examples['ReviewResponse']['scope']['source_revision']
assert examples['ReviewResponse']['responses'][0]['evidence_refs'][0]['artifact']['sha256'] == examples['ReviewResponse']['scope']['source_revision']
print(json.dumps({'contracts_valid': checked, 'standalone_schemas_valid': checked, 'invalid_examples_rejected':checked*2, 'static_links_valid':len(parser.links), 'version_examples':'OK'}, ensure_ascii=False))

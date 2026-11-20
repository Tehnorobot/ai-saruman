"""Immutable design attempts, including the drafts that failed validation."""

from hashlib import sha256
import json
from pathlib import Path
from time import time_ns
from uuid import uuid4


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return sha256(raw).hexdigest()


class DesignHistory:
    def __init__(self, directory, project, scope, run_id=None):
        self.directory = Path(directory) / sha256(project.encode()).hexdigest() if directory is not None else None
        self.project, self.scope, self.run_id = project, scope, run_id
        self.recent = []
        self.approved = None
        if self.directory is not None:
            for path in reversed(sorted(self.directory.glob('*.json'))):
                row = json.loads(path.read_text(encoding='utf-8'))
                if row.get('outcome') == 'confirmed' and self.approved is None:
                    self.approved = row
                if row.get('scope') == scope and row.get('outcome') == 'rejected' and len(self.recent) < 12:
                    self.recent.append(row)
                if self.approved is not None and len(self.recent) == 12:
                    break
            self.recent.reverse()

    def add(self, outcome, **data):
        row = {'schema': 'mage_home.design_attempt/1', 'projectId': self.project, 'runId': self.run_id,
               'scope': self.scope, 'outcome': outcome, 'timeNs': time_ns(), **data}
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f'{row["timeNs"]:020d}-{uuid4().hex}.json'
            draft = path.with_suffix('.tmp')
            draft.write_text(json.dumps(row, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            draft.replace(path)
        if outcome == 'rejected':
            self.recent.append(row)
            self.recent = self.recent[-12:]

"""Image-owned CLI for the same scoped context tools used by native agents.

Example: python -m agent.codex_tools query_memory '{"filters":{"task_ids":["W1"]}}'
The process inherits its container isolation; this CLI never supplies host mounts.
"""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys

from common import agent_context

TOOLS={'read_requirements','query_memory','read_artifact','read_diff','record_note','read_execution','list_files','run_command'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool',choices=sorted(TOOLS))
    parser.add_argument('arguments',nargs='?',default='-',help='JSON object, or - to read stdin')
    parser.add_argument('--workspace',default='.')
    parser.add_argument('--context',required=True,help='Controller role context JSON path inside this workspace')
    options=parser.parse_args(argv)
    root=Path(options.workspace).resolve()
    if not root.is_relative_to(Path.cwd().resolve()):
        raise ValueError('Tool workspace must remain inside the current project directory')
    arguments=json.loads(sys.stdin.read() if options.arguments=='-' else options.arguments)
    if not isinstance(arguments,dict):raise ValueError('Tool arguments must be an object')
    context=agent_context.read_json(root,options.context)
    if context.get('role') not in ('planner','coder','reviewer') or type(context.get('readonly')) is not bool:
        raise ValueError('Controller role and readonly context are required')
    tid=os.environ.get('LAB_ACTIVE_TASK_ID')
    if tid:
        import re
        if not re.fullmatch(r'[a-f0-9]{14}-round[0-9]{2,16}(?:-[a-f0-9]{32})?',tid):raise ValueError('Invalid active task identity')
        mailbox=Path(os.environ.get('AGENT_TASKS','/agent-tasks'))
        trusted=agent_context.read_json(mailbox,'running/'+tid+'.json')
        if any(context.get(key)!=trusted.get(key) for key in ('project_id','job_id','role','continuation_key')):
            raise ValueError('Tool context does not match the active authenticated task')
        if trusted.get('output_contract')=='SubmissionPatch' and not context['readonly']:
            raise ValueError('Format repair context must be read-only')
    from agent.dslab import Executor
    task={key:context[key] for key in ('scope','role','project_id','job_id','continuation_key') if key in context}
    executor=Executor(task,root)
    with redirect_stdout(io.StringIO()):
        result=executor.tool(options.tool,arguments,agent='main',readonly=context['readonly'] or task['role'] in ('planner','reviewer'))
    print(result)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,KeyError,TypeError) as error:
        print(json.dumps({'error':type(error).__name__,'detail':str(error),'meaning':'No acceptance verdict inferred.'}),file=sys.stderr)
        raise SystemExit(1)

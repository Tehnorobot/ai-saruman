"""Regenerate model-facing artifacts from the canonical contracts and codec.

Run from the project root with the project's Python dependencies installed.
This maintenance command is never called while processing a job.
"""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from common.agent_protocol import TARGETS,compact_schema,to_wire


def portable(node):
    result={k:v for k,v in node.items() if k in ('type','description','required','additionalProperties','$ref','enum')}
    if 'const' in node:
        value=node['const'];result['enum']=[value]
        result.setdefault('type','boolean' if isinstance(value,bool) else 'integer' if isinstance(value,int) else 'string')
    for key in ('properties','$defs'):
        if key in node:result[key]={k:portable(v) for k,v in node[key].items()}
    if 'properties' in result:result['required']=list(result['properties'])
    if 'items' in node:result['items']=portable(node['items'])
    for key in ('anyOf','oneOf','allOf'):
        if key in node:result[key]=[portable(v) for v in node[key]]
    return result


def generate():
    out=ROOT/'schemas/pipeline'
    for name,target in TARGETS.items():
        base=json.loads((out/(target+'.schema.json')).read_text(encoding='utf-8'))
        schema=compact_schema(name,base)
        example=to_wire(json.loads((out/'examples'/(target+'.json')).read_text(encoding='utf-8')))
        for path,value in ((out/(name+'.schema.json'),schema),
                           (out/(name+'.submission.schema.json'),portable(schema)),
                           (out/'examples'/(name+'.json'),example)):
            path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Generated',len(TARGETS),'compact agent contracts')


if __name__=='__main__':generate()

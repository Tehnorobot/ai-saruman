"""Install derived navigation while jobs are idle; never resume or rewrite proof."""
import hashlib,json
from pathlib import Path
from common import db,pipeline_memory,acceptance
from common.memory_archive import read_json,write_json
from common.project_lock import project_lock
from common.verification_feedback import execution_diagnostics,export_feedback

projects=Path('/projects');artifacts=Path('/artifacts')
with db.connect() as connection:jobs=[dict(row) for row in connection.execute('SELECT * FROM jobs')]
assert all(job['status'] in ('USER_PAUSED','FAILED','STOPPED','FINISHED') for job in jobs),'Active jobs'
results=[]
for job in jobs:
    output=artifacts/job['project_id']/job['id'];ws=projects/job['project_id']/'workspace'
    state=read_json(output,'pipeline/state.json');public=read_json(output,'pipeline/public.json')
    latest=read_json(output,'latest_verification.json');proof=read_json(output,'pipeline/latest-result.json')
    if not state or not public or not ws.is_dir():continue
    if state.get('optimized_context_backfill')=='optimized-roles-v1':continue
    with project_lock(ws.parent):
        names=[ws.parent/'ТЗ.md',output/'pipeline/catalog.json',output/'pipeline/graph.json',output/'pipeline/latest-result.json']
        before={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in names if path.is_file()}
        scope={**public['scope'],'source_revision':acceptance.source_fingerprint(ws)}
        diagnostics=[]
        if proof and latest:
            diagnostics=execution_diagnostics(ws,output,latest,proof['scope']['round'],scope=proof['scope'])
            export_feedback(ws,output,latest,proof['scope']['round'])
            key=hashlib.sha256(json.dumps({'run':proof.get('runner_run_id'),'scope':proof.get('scope')},
                sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            write_json(output,'pipeline/diagnostic-navigation-'+key+'.json',{
                'execution_diagnostics':diagnostics,'original_scope':proof['scope'],
                'meaning':'Derived diagnostic navigation only. Original acceptance history unchanged.'})
        state['execution_diagnostics']=diagnostics
        state['optimized_context_backfill']='optimized-roles-v1'
        note={'artifact_type':'RuntimeUpgrade','summary':'Optimized role context and evidence navigation available',
            'runtime':'optimized-roles-v1','primary_diagnostic_refs':diagnostics,
            'instruction':'On resume refresh only next-work selection using actual primary failure and implementation availability. '
                'Reuse current specification/catalog/graph and existing partial code. Unstarted tests are not failed product experiments. '
                'Use scoped retrieval, public hypothesis/finding notes and actual source diffs; no task quota or new agent.',
            'meaning':'Maintenance handoff, not product requirements or independent PASS.'}
        record=pipeline_memory.append_event(output,ws,'reflection',note,scope=scope,producer='controller',tags=['untested'])
        state.setdefault('events',[]).append(record['record_id']);state['last_event_record']=record['record_id']
        write_json(output,'pipeline/state.json',state)
        public['execution_diagnostics']=diagnostics;write_json(output,'pipeline/public.json',public)
        after={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in names if path.is_file()}
        assert before==after,'Normative/canonical result mutation'
        results.append({'job_id':job['id'],'status':job['status'],'round':job['round'],
            'diagnostic_count':len(diagnostics),'maintenance_record':record['record_id'],'canonical_inputs_results_unchanged':True})
with db.connect() as connection:after={row['id']:dict(row) for row in connection.execute('SELECT * FROM jobs')}
assert all(all(after[j['id']][key]==j[key] for key in ('status','phase','round','resume_generation','pause_requested')) for j in jobs)
print(json.dumps({'jobs_unchanged':True,'jobs_resumed':False,'model_invoked':False,'projects':results},ensure_ascii=False))

"""Exercise the controller through fake model and trusted-runner boundaries.

No provider calls, game, Docker or fabricated production acceptance is used.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import acceptance, pipeline_memory
from common.pipeline_contracts import load_example, validate
from worker import pipeline


class FakeEngine:
    COMPACT_PROTOCOL=False  # Explicit legacy contract compatibility suite.
    AGENT_TIMEOUT=3600
    TEST_TIMEOUT=120
    EULA=False
    DockerSandbox=object
    def __init__(self,ws):
        self.ws=ws;self.requests=[];self.updates=[];self.status=None;self.invalid_plans=0;self.verifications=0;self.reject_final_review=False;self.question_kind=None
    def poll_job(self,jid):return self.status
    def job_update(self,jid,**fields):self.updates.append(fields)
    def snapshot_request(self,*args):pass
    def cancel_agent_tasks(self,*args):pass
    def retire_attempt(self,*args):pass
    def agent_error(self,result):return result.get('tail','error')
    def request_agent(self,jid,pid,number,prompt,logfile,**kwargs):
        context=json.loads(prompt.split('Input JSON:\n',1)[1]);scope=context['scope'];name=kwargs['output_contract']
        self.requests.append((name,kwargs['role'],deepcopy(context)))
        obj=load_example(name);obj['scope']=deepcopy(scope)
        spec=self.ws/'.lab-pipeline/spec.md'
        quoted={'artifact':pipeline.artifact(self.ws,'.lab-pipeline/spec.md','text/markdown'),
                'start_line':1,'end_line':2,'excerpt':spec.read_text(encoding='utf-8').rstrip()}
        if name=='SubmissionPatch':
            from common.correction_journal import select
            repair=context['input'];target=load_example(repair['target_contract']);target['scope']=deepcopy(scope)
            for key,value in repair.get('trusted_references',{}).items():
                if key in target:target[key]=deepcopy(value)
            if repair['target_contract']=='PlannerDecision':target['actions'][0]['task_ids']=['T1','T2']
            obj={'contract':'SubmissionPatch','version':'v1','submission_sha256':repair['submission_sha256'],'changes':[]}
            for field in repair['fields']:
                obj['changes'].append({'path':field['path'],'operation':'set','expected_exists':field['exists'],
                    'expected_old_json':json.dumps(field['saved_value']['value']),
                    'new_json':json.dumps(select(target,field['path']))})
        elif name=='PreparationHandoff':
            obj['catalog_ref']=context['input']['catalog_ref'];obj['graph_ref']=context['input']['graph_ref']
            obj['unresolved_review_refs']=context['input']['unresolved_review_refs']
            for task in obj['tasks']:task.update(status='ready',experiment=None)
            catalog=json.loads(pipeline.source_bytes(self.ws,obj['catalog_ref']))
            graph=json.loads(pipeline.source_bytes(self.ws,obj['graph_ref']))
            technical={cid for q in catalog.get('question_resolutions',[]) if q['kind']=='experiment' and q['status']=='open' for cid in q['condition_ids']}
            for task in obj['tasks']:
                conditions=next(t['condition_ids'] for t in graph['tasks'] if t['id']==task['task_id'])
                if set(conditions)&technical:task.update(status='experiment',experiment='Observe the actual condition using an independent test')
        elif name=='CatalogPatch':
            obj['base_catalog_ref']=context['input']['base_catalog_ref']
            base=json.loads(pipeline.source_bytes(self.ws,obj['base_catalog_ref']))
            obj.update(requirements_upsert=[],requirements_remove=[],conditions_upsert=[],conditions_remove=[],
                       questions_add=[],question_resolutions_upsert=[],unclassified_add=[],unclassified_remove=[],addressed_finding_ids=[])
            if self.question_kind:
                kind=self.question_kind
                obj['question_resolutions_upsert']=[{'question':q,'kind':kind,
                    'status':'resolved' if kind=='implementation_decision' else 'open',
                    'owner':{'implementation_decision':'planner','experiment':'coder','user_clarification':'user'}[kind],
                    'condition_ids':['C1'],'answer':'Documented implementation choice' if kind=='implementation_decision' else None,
                    'reason':'Fixture explicit rationale','source_refs':[deepcopy(quoted)]} for q in base['open_questions']]
        elif name=='RequirementsCatalog':
            for requirement in obj['requirements']:requirement['source_refs']=[deepcopy(quoted)]
            if self.question_kind:
                kind=self.question_kind;obj['open_questions']=['Fixture unresolved choice']
                obj['question_resolutions']=[{'question':obj['open_questions'][0],'kind':kind,
                    'status':'resolved' if kind=='implementation_decision' else 'open',
                    'owner':{'implementation_decision':'planner','experiment':'coder','user_clarification':'user'}[kind],
                    'condition_ids':['C1'],'answer':'Documented implementation choice' if kind=='implementation_decision' else None,
                    'reason':'Fixture explicit rationale','source_refs':[deepcopy(quoted)]}]
        elif name=='TaskGraph':
            obj['catalog_ref']=pipeline.artifact(self.ws,'.lab-pipeline/catalog.json')
        elif name=='PlannerDecision':
            obj.update(strategy='implement',rationale='Implement the related functions together',hypotheses=[],deferred=[],manual_checks=[],replacement_graph_ref=None)
            obj['actions']=[{'id':'A1','kind':'implement','task_ids':['T1','T2'],'condition_ids':['C1','C2'],
                'goal':'Implement both source requirements','expected_observation':'Both independent conditions pass','depends_on_action_ids':[]}]
            if self.invalid_plans:
                self.invalid_plans-=1;obj['actions'][0]['task_ids']='T1'
        elif name=='ReviewResult':
            obj.update(disposition='ready_for_verification',findings=[],unknowns=[],reviewed_condition_ids=['C1','C2'],evidence_refs=[quoted])
            obj['request_ref']=context['input']['context_ref']
            if context['input']['subject']=='scenarios' and self.reject_final_review:
                obj['disposition']='changes_requested'
                finding=load_example('ReviewResult')['findings'][0]
                finding['code_refs']=[quoted];finding['observation']='Fixture: coverage needs a stronger test'
                obj['findings']=[finding]
            if context['input']['subject'] in ('code','scenarios'):
                path='src/gametest/PlantTest.java';text=(self.ws/path).read_text()
                obj['evidence_refs'].append({'artifact':pipeline.artifact(self.ws,path),'start_line':1,'end_line':1,'excerpt':text})
        elif name=='CoderReport':
            test=self.ws/'src/gametest/PlantTest.java';test.parent.mkdir(parents=True,exist_ok=True);test.write_text('class PlantTest { /* deterministic fixture */ }',encoding='utf-8')
            source=acceptance.source_fingerprint(self.ws)
            obj['base_source_revision']=context['input']['assignment']['scope']['source_revision']
            obj['scope']['source_revision']=source
            manifest=load_example('ScenarioManifest');manifest['scope']=deepcopy(obj['scope'])
            manifest['scenarios'][0]['test_file']=pipeline.artifact(self.ws,'src/gametest/PlantTest.java')
            manifest['scenarios'][0]['execution']['kind']='unit';manifest['scenarios'][0]['media']=[]
            obj['scenario_manifest_ref']=pipeline.export_json(self.ws,'scenarios-proposed.json',manifest)
            obj.update(action_ids=['A1'],changed_files=[pipeline.artifact(self.ws,'src/gametest/PlantTest.java')],review_response_refs=[])
            obj['plan_ref']=context['input']['assignment']['plan_ref']
        logfile.parent.mkdir(parents=True,exist_ok=True)
        observed=quoted['excerpt']+'\n'+((self.ws/'src/gametest/PlantTest.java').read_text() if (self.ws/'src/gametest/PlantTest.java').exists() else '')
        events=[{'type':'item.completed','item':{'type':'tool_result','text':observed}},
                {'type':'item.completed','item':{'type':'agent_message','agent_id':kwargs['role'],'text':json.dumps(obj)}}]
        logfile.write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
        return {'status':'PASS','label':kwargs['role'],'role':kwargs['role'],'provider':'codex','model':'fixture'}


class CycleTest(unittest.TestCase):
    def test_failed_or_raised_request_retains_corrections_and_next_success_closes_them(self):
        from common import correction_journal as cj
        for raises in (False,True):
            with self.subTest(raises=raises):
                cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
                original=self.engine.request_agent
                bad=cj.record('RequirementsCatalog',{'requirements':'bad'},[{'path':'$.requirements'}])
                def fail(*args,**kwargs):
                    result=original(*args,**kwargs)
                    logfile=args[4]
                    logfile.write_text(logfile.read_text()+'\n'+json.dumps({'type':'correction.record','record':bad}),encoding='utf-8')
                    if raises:raise pipeline.Interrupted('fixture pause')
                    return {**result,'status':'FAIL','error':'HTTP 429 quota exhausted'}
                self.engine.request_agent=fail
                label='journal-failure-'+str(raises)
                try:
                    with self.assertRaises((pipeline.AgentFailure,pipeline.Interrupted)):
                        cycle.ask('RequirementsCatalog','planner',{},label)
                finally:self.engine.request_agent=original
                self.assertTrue(any(p['correction']['correction_id']==bad['correction_id'] for _,p in cycle.correction_records('RequirementsCatalog')))
                cycle.ask('RequirementsCatalog','planner',{},label)
                records=cycle.correction_records('RequirementsCatalog')
                self.assertTrue(any(bad['correction_id'] in p['correction']['resolves']
                    and p['correction']['outcome']=='accepted_controller' for _,p in records))

    def test_correction_import_survives_restart_without_duplicates_and_exports_links(self):
        from common import correction_journal as cj
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        bad=cj.record('ReviewResult',{'ids':'bad'},[{'path':'$.ids'}])
        logfile=self.root/'attempt.jsonl'
        logfile.write_text(json.dumps({'type':'correction.record','record':bad})+'\n'+
            json.dumps({'type':'item.completed','item':{'text':json.dumps({'type':'correction.record','record':bad})}}),encoding='utf-8')
        cycle.import_corrections(logfile,'ReviewResult','phase1')
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        cycle.import_corrections(logfile,'ReviewResult','phase1')
        self.assertEqual(len(cycle.correction_records('ReviewResult')),1)
        index=json.loads(pipeline.source_bytes(self.ws,cycle.correction_index('ReviewResult','phase1')))
        self.assertEqual(index['open_api_correction_ids'],[bad['correction_id']])
        self.assertEqual(json.loads(pipeline.source_bytes(self.ws,cycle.correction_index('ReviewResult','other')))['open_api_correction_ids'],[])
        good=cj.record('ReviewResult',{'ids':[]},[],outcome='accepted_format',resolves=[bad['correction_id']])
        cycle.store_correction(good,'phase1')
        index=json.loads(pipeline.source_bytes(self.ws,cycle.correction_index('ReviewResult','phase1')))
        self.assertEqual(index['open_api_correction_ids'],[])
        self.assertEqual(len(index['records']),2)
        self.assertEqual(index['records'][1]['resolves'],[bad['correction_id']])
        cycle.scope={**cycle.scope,'spec_revision':'f'*64}
        self.assertEqual(cycle.correction_records('ReviewResult'),[])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'project';self.project.mkdir();self.ws=self.project/'workspace';self.ws.mkdir()
        (self.project/'ТЗ.md').write_text('Plant a sapling.\nGrow a tree.\n',encoding='utf-8')
        (self.ws/'existing.java').write_text('preserve existing source',encoding='utf-8')
        self.out=self.root/'artifacts';self.out.mkdir()
        self.job={'id':'1'*14,'project_id':'2'*14,'round':0,'max_rounds':1,'provider':'codex','model':'',
                  'reasoning_effort':'default','role_models':'{}','dslab_token_budget':100000,'dslab_max_calls':100,'dslab_max_output_tokens':8000}
        self.engine=FakeEngine(self.ws)

    def verified(self,ws,stage,jid,catalog,manifest,decision,**kwargs):
        self.engine.verifications+=1
        scope={**catalog['scope'],'source_revision':acceptance.source_fingerprint(ws),'round':1}
        ref=pipeline.export_json(ws,'test-result-ref.json',{'test_fixture':True})
        result=pipeline.message('VerificationResult',scope=scope,request_ref=ref,runner_run_id='fixture-'+str(self.engine.verifications),build_status='PASS',
            scenario_results=[{'scenario_id':s['id'],'status':'PASS','checkpoints':[{'checkpoint_id':cp['id'],'status':'PASS','actual':'fixture independent case passed','evidence_refs':[]} for cp in s['checkpoints']],'media_refs':[]} for s in manifest['scenarios']],
            infrastructure_errors=[],raw_report_refs=[])
        return result,{'status':'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED','checks':[{'label':'gradle_build','status':'PASS'}]}

    def execute(self):
        with patch('worker.pipeline_verification.verify',side_effect=self.verified):
            pipeline.run(self.engine,self.job,self.ws,self.out,{})

    def test_complete_cycle_requires_review_and_full_independent_run(self):
        self.execute()
        self.assertEqual(self.engine.verifications,2)
        names=[r[0] for r in self.engine.requests]
        self.assertEqual(names,['RequirementsCatalog','ReviewResult','TaskGraph','ReviewResult','PlannerDecision','CoderReport','ReviewResult','ReviewResult'])
        public=json.loads((self.out/'pipeline/public.json').read_text())
        validate('ProjectStatus',public['status'])
        self.assertTrue(public['status']['ready'])
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual((self.ws/'existing.java').read_text(),'preserve existing source')
        records=pipeline_memory.query(self.out,page_size=100)
        self.assertGreater(len(records['records']),10)
        for item in records['records']:
            self.assertIsNotNone(pipeline_memory.read_payload(self.out,item['record_id']))

    def test_soft_limit_transitions_to_real_graph_handoff_and_coder_without_full_approval(self):
        original=self.engine.request_agent
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='RequirementsCatalog':
                cycle.state['preparation'].update(elapsed_seconds=3601,handoff_due=True)
            return result
        self.engine.request_agent=request
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        names=[n for n,_,_ in self.engine.requests]
        self.assertEqual(names,['RequirementsCatalog','TaskGraph','PreparationHandoff'])
        draft=pipeline.read_json(self.out,'pipeline/draft.json')
        self.assertEqual(draft['progress']['semantic_review'],'deferred_to_work')
        self.assertFalse(pipeline.read_json(self.out,'pipeline/public.json')['status']['ready'])
        decision,graph=cycle.plan(catalog,graph,{})
        cycle.code_and_review(catalog,graph,decision)
        self.assertTrue(any(n=='CoderReport' for n,_,_ in self.engine.requests))
        self.assertFalse(any(c['status']=='verified' for c in cycle.coverage(catalog)))

    def test_soft_limit_stops_catalog_revision_polishing_keeps_review_findings(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        original=self.engine.request_agent
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            context=json.loads(args[3].split('Input JSON:\n',1)[1])
            if kwargs['output_contract']=='ReviewResult' and context['input']['subject']=='catalog':
                path=args[4];events=[json.loads(line) for line in path.read_text().splitlines()]
                obj=json.loads(events[-1]['item']['text']);obj['disposition']='changes_requested'
                finding=load_example('ReviewResult')['findings'][0];finding['code_refs']=obj['evidence_refs'];obj['findings']=[finding]
                events[-1]['item']['text']=json.dumps(obj);path.write_text('\n'.join(json.dumps(e) for e in events))
                cycle.state['preparation'].update(elapsed_seconds=3601,handoff_due=True)
            return result
        self.engine.request_agent=request;cycle.decomposition()
        self.assertFalse(any(n=='CatalogPatch' for n,_,_ in self.engine.requests))
        self.assertTrue(cycle.state['preparation_handoff']['unresolved_review_refs'])
        self.assertEqual(cycle.state['decomposition_review']['disposition'],'changes_requested')

    def test_soft_code_review_handoff_retains_findings_and_returns_real_scenarios(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        decision,graph=cycle.plan(catalog,graph,{})
        original=self.engine.request_agent
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            context=json.loads(args[3].split('Input JSON:\n',1)[1])
            if kwargs['output_contract']=='ReviewResult' and context['input']['subject']=='code':
                path=args[4];events=[json.loads(line) for line in path.read_text().splitlines()]
                value=json.loads(events[-1]['item']['text']);value['disposition']='changes_requested'
                finding=load_example('ReviewResult')['findings'][0]
                finding['requirement_ids']=['R1'];finding['code_refs']=value['evidence_refs'];value['findings']=[finding]
                events[-1]['item']['text']=json.dumps(value)
                path.write_text('\n'.join(json.dumps(e) for e in events))
                cycle.state['coding_review_handoff']['elapsed_seconds']=3601
            return result
        self.engine.request_agent=request
        report,manifest,review=cycle.code_and_review(catalog,graph,decision)
        self.assertTrue(manifest['scenarios'])
        self.assertTrue(report)
        self.assertEqual(review['disposition'],'changes_requested')
        self.assertTrue(review['findings'])
        self.assertEqual(sum(n=='CoderReport' for n,_,_ in self.engine.requests),1)
        self.assertIn('Soft coding/review',cycle.state['reflection']['reason'])
        self.assertFalse(any(c['status']=='verified' for c in cycle.coverage(catalog)))
        resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        self.assertTrue(resumed.coding_review_handoff_due())
        fresh=pipeline.Cycle(self.engine,self.job,self.ws,self.out,2)
        self.assertFalse(fresh.coding_review_handoff_due())

    def test_active_preparation_clock_survives_resume_and_excludes_idle_time(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        cycle._preparing=True;cycle._preparation_tick=100
        with patch('worker.pipeline.time.monotonic',return_value=110):cycle.save()
        self.assertEqual(cycle.state['preparation']['elapsed_seconds'],10)
        cycle._preparing=False
        with patch('worker.pipeline.time.monotonic',return_value=100000):
            resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
            resumed.save()
        self.assertEqual(resumed.state['preparation']['elapsed_seconds'],10)

    def test_old_aggregate_timeout_migrates_to_soft_handoff_due(self):
        self.job['last_error']='Configured cycle execution time elapsed; checkpoints preserved'
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        self.assertTrue(cycle.handoff_due())
        cycle.started-=100000
        catalog,graph=cycle.decomposition()
        self.assertTrue(cycle.state['preparation_handoff'])

    def test_experiment_handoff_is_refreshed_after_new_independent_result(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        cycle.ensure_handoff(catalog,graph)
        first=cycle.state['preparation_handoff_binding'];count=len(self.engine.requests)
        cycle.ensure_handoff(catalog,graph)
        self.assertEqual(count,len(self.engine.requests))
        pipeline.write_json(self.out,'pipeline/latest-result.json',{'scope':cycle.scope,'new_independent_observation':'fixture'})
        cycle.ensure_handoff(catalog,graph)
        self.assertNotEqual(first,cycle.state['preparation_handoff_binding'])
        self.assertEqual(len(self.engine.requests),count+1)

    def test_soft_handoff_keeps_latest_amended_draft_instead_of_original_inventory(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        catalog['conditions'][0]['expected']='Accepted amended condition must survive handoff'
        pipeline.write_json(self.out,'pipeline/draft.json',{'scope':cycle.scope,'catalog':catalog,
            'graph':None,'progress':{'semantic_review':'pending'}})
        # Emulate an interrupted preparation that has not published canonical files.
        (self.out/'pipeline/catalog.json').unlink();(self.out/'pipeline/graph.json').unlink()
        cycle.state['preparation'].update(elapsed_seconds=3601,handoff_due=True);cycle.save()
        original_calls=sum(n=='RequirementsCatalog' for n,_,_ in self.engine.requests)
        resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        adopted,_=resumed.decomposition()
        self.assertEqual(adopted['conditions'][0]['expected'],catalog['conditions'][0]['expected'])
        self.assertEqual(sum(n=='RequirementsCatalog' for n,_,_ in self.engine.requests),original_calls)

    def test_soft_handoff_still_requires_full_final_review_and_verification(self):
        self.job['last_error']='Configured cycle execution time elapsed; checkpoints preserved'
        self.execute()
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual(self.engine.verifications,2)
        self.assertTrue(any(n=='ReviewResult' and c['input']['subject']=='scenarios' for n,_,c in self.engine.requests))
        from app import main as application
        with patch.object(application,'pipeline_root',return_value=({'status':'FINISHED','last_error':None},self.out)):
            self.assertTrue(application.job_pipeline(self.job['id'])['status']['ready'])

    def test_soft_handoff_does_not_accept_invalid_graph_under_time_pressure(self):
        self.job['last_error']='Configured cycle execution time elapsed; checkpoints preserved'
        original=self.engine.request_agent;fail=[True]
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='TaskGraph' and fail:
                fail.pop();path=args[4];events=[json.loads(line) for line in path.read_text().splitlines()]
                obj=json.loads(events[-1]['item']['text']);obj['tasks'][0]['depends_on']=['unknown']
                events[-1]['item']['text']=json.dumps(obj);path.write_text('\n'.join(json.dumps(e) for e in events))
            return result
        self.engine.request_agent=request
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1);cycle.decomposition()
        self.assertEqual(sum(n=='TaskGraph' for n,_,_ in self.engine.requests),1)
        self.assertEqual(sum(n=='SubmissionPatch' for n,_,_ in self.engine.requests),1)
        self.assertTrue(cycle.state['preparation_handoff'])

    def test_post_result_question_resolution_can_close_experiment_after_soft_cutoff(self):
        self.engine.question_kind='experiment'
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        pipeline.write_json(self.out,'pipeline/latest-result.json',{'scope':cycle.scope,'scenario_results':[{'status':'PASS'}]})
        original=self.engine.request_agent
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='CatalogPatch':
                path=args[4];events=[json.loads(line) for line in path.read_text().splitlines()]
                value=json.loads(events[-1]['item']['text'])
                for q in value['question_resolutions_upsert']:q.update(status='resolved',answer='Observed independent result supports this choice')
                events[-1]['item']['text']=json.dumps(value);path.write_text('\n'.join(json.dumps(e) for e in events))
            return result
        self.engine.request_agent=request
        cycle.ensure_handoff(catalog,graph)
        from common.task_graph import pending_questions
        self.assertEqual(pending_questions(catalog),[])
        self.assertFalse(any(c['status']=='verified' for c in cycle.coverage(catalog)))

    def test_soft_cutoff_classifies_questions_missing_legacy_resolution_records(self):
        self.engine.question_kind='implementation_decision'
        original=self.engine.request_agent
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='RequirementsCatalog':
                path=args[4];events=[json.loads(line) for line in path.read_text().splitlines()]
                value=json.loads(events[-1]['item']['text']);value['question_resolutions']=[]
                events[-1]['item']['text']=json.dumps(value);path.write_text('\n'.join(json.dumps(e) for e in events))
            return result
        self.engine.request_agent=request
        self.job['last_error']='Configured cycle execution time elapsed; checkpoints preserved'
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,_=cycle.decomposition()
        self.assertEqual(catalog['question_resolutions'][0]['status'],'resolved')
        self.assertTrue(cycle.state['preparation_handoff'])

    def test_new_review_is_not_lost_when_catalog_and_graph_are_unchanged(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();cycle.ensure_handoff(catalog,graph)
        first=cycle.state['preparation_handoff_binding']
        review=load_example('ReviewResult');review['disposition']='changes_requested'
        cycle.ensure_handoff(catalog,graph,review)
        self.assertNotEqual(first,cycle.state['preparation_handoff_binding'])
        self.assertTrue(cycle.state['preparation_handoff']['unresolved_review_refs'])

    def test_catalog_review_correction_uses_delta_and_reuses_it_on_resume(self):
        original=self.engine.request_agent;reject=[True];review_contexts=[]
        def request(*args,**kwargs):
            context=json.loads(args[3].split('Input JSON:\n',1)[1])
            if kwargs['output_contract']=='ReviewResult' and context['input']['subject']=='catalog':
                review_contexts.append(json.loads(pipeline.source_bytes(self.ws,context['input']['context_ref'])))
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='ReviewResult' and context['input']['subject']=='catalog' and reject:
                reject.pop();path=args[4]
                events=[json.loads(line) for line in path.read_text().splitlines()]
                value=json.loads(events[-1]['item']['text']);value['disposition']='changes_requested'
                finding=load_example('ReviewResult')['findings'][0]
                finding['requirement_ids']=['R1'];finding['code_refs']=value['evidence_refs'];value['findings']=[finding]
                events[-1]['item']['text']=json.dumps(value)
                path.write_text('\n'.join(json.dumps(e) for e in events))
            if kwargs['output_contract']=='CatalogPatch':
                self.assertNotIn('catalog',context['input'])
                path=args[4];events=[json.loads(line) for line in path.read_text().splitlines()]
                value=json.loads(events[-1]['item']['text'])
                base=json.loads(pipeline.source_bytes(self.ws,context['input']['base_catalog_ref']))
                condition=deepcopy(base['conditions'][0]);condition['expected']='The planted sapling is observable at its position'
                value['conditions_upsert']=[condition];value['addressed_finding_ids']=['F1']
                events[-1]['item']['text']=json.dumps(value)
                path.write_text('\n'.join(json.dumps(e) for e in events))
            return result
        self.engine.request_agent=request
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        self.assertEqual(sum(n=='RequirementsCatalog' for n,_,_ in self.engine.requests),1)
        self.assertEqual(sum(n=='CatalogPatch' for n,_,_ in self.engine.requests),1)
        self.assertEqual(review_contexts[-1]['required_condition_ids'],['C1'])
        self.assertFalse(review_contexts[-1]['full_review'])
        delta_path=self.out/'round-01/pipeline/catalog-review-1/complete.json'
        self.assertTrue(delta_path.is_file())
        self.assertEqual(len(review_contexts),2)
        self.assertEqual(self.engine.requests[2][0],'CatalogPatch')
        saved=len(self.engine.requests)
        pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        self.assertEqual(len(self.engine.requests),saved)

    def test_accepted_legacy_full_amendment_checkpoint_is_reused_without_model(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();inputs={'scope':cycle.scope,'catalog':catalog,'review':None}
        pipeline.write_json(cycle.stage,'pipeline/legacy-amendment/complete.json',{'input_binding':pipeline.input_binding(inputs),'value':catalog})
        count=len(self.engine.requests)
        result=cycle.amend_catalog(catalog,lambda ref:pipeline.source_bytes(self.ws,ref),'legacy-amendment',legacy_input=inputs)
        self.assertEqual(result,catalog);self.assertEqual(count,len(self.engine.requests))

    def test_repeated_bad_format_is_corrected_without_one_retry_stop(self):
        self.engine.invalid_plans=3
        self.execute()
        self.assertTrue(json.loads((self.out/'pipeline/public.json').read_text())['status']['ready'])
        self.assertEqual(sum(n=='PlannerDecision' for n,_,_ in self.engine.requests),1)
        self.assertEqual(sum(n=='SubmissionPatch' for n,_,_ in self.engine.requests),1)
        records=pipeline_memory.query(self.out,page_size=100)
        feedback=[pipeline_memory.read_payload(self.out,r['record_id']) for r in records['records'] if pipeline_memory.load_record(self.out,r['record_id'])['entry']['kind']=='validation']
        self.assertTrue(any(v.get('issues',[{}])[0].get('path')=='$.actions[0].task_ids' for v in feedback))

    def test_pause_does_not_call_model_or_change_source(self):
        self.engine.status='USER_PAUSED'
        pipeline.run(self.engine,self.job,self.ws,self.out,{})
        self.assertEqual(self.engine.requests,[])
        self.assertEqual((self.ws/'existing.java').read_text(),'preserve existing source')
        self.assertEqual(self.engine.updates[-1]['status'],'USER_PAUSED')

    def test_partial_success_and_failed_media_do_not_close_condition(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();decision,graph=cycle.plan(catalog,graph,{})
        _,manifest,_=cycle.code_and_review(catalog,graph,decision)
        result,legacy=self.verified(self.ws,cycle.stage,self.job['id'],catalog,manifest,decision)
        result['scenario_results'][0]['status']='ERROR'
        cycle.record_verification(catalog,manifest,decision,result,legacy)
        self.assertFalse(any(c['status']=='verified' for c in cycle.coverage(catalog)))

    def test_final_review_failure_is_not_readiness_or_a_manual_pause(self):
        self.engine.reject_final_review=True
        self.execute()
        value=json.loads((self.out/'pipeline/public.json').read_text())
        self.assertFalse(value['status']['ready'])
        self.assertEqual(self.engine.verifications,1)
        self.assertEqual(self.engine.updates[-1]['phase'],'max_rounds_reached')
        self.assertFalse(any(x.get('status')=='USER_PAUSED' for x in self.engine.updates))

    def test_changed_condition_definition_requires_new_proof(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();decision,graph=cycle.plan(catalog,graph,{})
        _,manifest,_=cycle.code_and_review(catalog,graph,decision)
        result,legacy=self.verified(self.ws,cycle.stage,self.job['id'],catalog,manifest,decision)
        cycle.record_verification(catalog,manifest,decision,result,legacy)
        self.assertTrue(all(c['status']=='verified' for c in cycle.coverage(catalog)))
        catalog['conditions'][0]['expected']='A materially different behavior'
        self.assertEqual(cycle.coverage(catalog)[0]['status'],'needs_retest')

    def test_independent_pass_is_preserved_when_a_later_condition_fails(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();decision,graph=cycle.plan(catalog,graph,{})
        _,manifest,_=cycle.code_and_review(catalog,graph,decision)
        result,legacy=self.verified(self.ws,cycle.stage,self.job['id'],catalog,manifest,decision)
        result['scenario_results'][0]['status']='FAIL'
        result['scenario_results'][0]['checkpoints'][2].update(status='FAIL',actual='Tree did not grow')
        cycle.record_verification(catalog,manifest,decision,result,legacy)
        status={c['condition_id']:c['status'] for c in cycle.coverage(catalog)}
        self.assertEqual(status,{'C1':'verified','C2':'failed'})
        proof=cycle.coverage(catalog)[0]['result_refs'][0]
        # A later export cannot overwrite a condition's original proof.
        pipeline.export_json(self.ws,'latest-result.json',{'newer':'result'})
        self.assertEqual(pipeline.artifact(self.ws,proof['path'])['sha256'],proof['sha256'])

    def test_user_input_is_the_only_normative_catalog_source(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        catalog['requirements'][0]['source_refs'][0]['artifact']=pipeline.artifact(self.ws,'existing.java')
        pipeline.write_json(self.out,'pipeline/catalog.json',catalog)
        restored,_=cycle.decomposition()
        self.assertTrue(all(ref['artifact']['path']=='.lab-pipeline/spec.md'
                            for requirement in restored['requirements'] for ref in requirement['source_refs']))
        payloads=[pipeline_memory.read_payload(self.out,row['record_id'])
                  for row in pipeline_memory.query(self.out,page_size=100)['records']]
        self.assertTrue(any(p.get('checkpoint')=='catalog' and p.get('original')==catalog for p in payloads))

    def test_recovery_after_coding_does_not_repeat_coder(self):
        failed=[True]
        def interrupt_once(*args,**kwargs):
            if failed.pop() if failed else False:raise pipeline.Interrupted('USER_PAUSED')
            return self.verified(*args,**kwargs)
        with patch('worker.pipeline_verification.verify',side_effect=interrupt_once):
            pipeline.run(self.engine,self.job,self.ws,self.out,{})
        self.engine.cancel_agent_tasks=lambda *a:None
        calls=sum(n=='CoderReport' for n,_,_ in self.engine.requests)
        self.execute()
        self.assertEqual(sum(n=='CoderReport' for n,_,_ in self.engine.requests),calls)
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')

    def test_public_api_reads_canonical_memory_without_resuming_job(self):
        self.execute()
        from fastapi.testclient import TestClient
        import app.main as application
        row={**self.job,'status':'USER_PAUSED','phase':'interrupted','last_error':None}
        artifact_root=self.out.parent
        canonical=artifact_root/self.job['project_id']/self.job['id']
        # API normally derives this directory. Keep fixture files in place.
        canonical.parent.mkdir(parents=True,exist_ok=True)
        self.out.rename(canonical)
        with patch.object(application,'ARTIFACTS',artifact_root),patch.object(application.db,'job_row',return_value=row):
            client=TestClient(application.app);url='/api/jobs/'+self.job['id']+'/pipeline'
            response=client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['status']['stage'],'paused')
            page=client.get(url+'/memory').json()
            self.assertTrue(page['records'])
            record=client.get(url+'/memory/'+page['records'][0]['record_id'])
            self.assertEqual(record.status_code,200)
            self.assertIn('payload',record.json())
            self.assertEqual(client.get(url+'/memory/not-a-record').status_code,404)
        self.assertEqual(row['status'],'USER_PAUSED')

    def test_draft_catalog_is_visible_and_resume_cannot_bypass_review(self):
        original=self.engine.request_agent;interrupt=[True]
        def request(*args,**kwargs):
            if kwargs['output_contract']=='TaskGraph' and interrupt:
                interrupt.pop();raise pipeline.Interrupted('USER_PAUSED')
            return original(*args,**kwargs)
        self.engine.request_agent=request
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        with self.assertRaises(pipeline.Interrupted):cycle.decomposition()
        draft=pipeline.read_json(self.out,'pipeline/draft.json')
        self.assertTrue(draft['progress']['catalog_structure_valid'])
        self.assertEqual(draft['progress']['semantic_review'],'pending')
        self.assertIsNone(pipeline.read_json(self.out,'pipeline/catalog.json'))
        public=pipeline.read_json(self.out,'pipeline/public.json')
        self.assertTrue(public['catalog']['conditions']);self.assertIsNone(public['graph'])
        pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        self.assertEqual(sum(n=='RequirementsCatalog' for n,_,_ in self.engine.requests),1)
        self.assertEqual(sum(n=='ReviewResult' and c['input']['subject']=='catalog' for n,_,c in self.engine.requests),1)
        self.assertTrue(any(n=='ReviewResult' and c['input']['subject']=='decomposition' for n,_,c in self.engine.requests))

    def test_graph_checkpoint_also_requires_semantic_review_after_interruption(self):
        original=self.engine.request_agent;interrupt=[True]
        def request(*args,**kwargs):
            if kwargs['output_contract']=='ReviewResult' and '"subject": "decomposition"' in args[3] and interrupt:
                interrupt.pop();raise pipeline.Interrupted('USER_PAUSED')
            return original(*args,**kwargs)
        self.engine.request_agent=request
        with self.assertRaises(pipeline.Interrupted):pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        self.assertIsNone(pipeline.read_json(self.out,'pipeline/catalog.json'))
        pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        self.assertEqual(sum(n=='TaskGraph' for n,_,_ in self.engine.requests),1)
        self.assertIsNotNone(pipeline.read_json(self.out,'pipeline/catalog.json'))

    def test_only_transient_stream_is_retried_with_same_budget(self):
        original=self.engine.request_agent;interrupt=[True];allowances=[]
        def request(*args,**kwargs):
            allowances.append(kwargs['allowance'])
            if kwargs['output_contract']=='TaskGraph' and interrupt:
                interrupt.pop();return {'status':'FAIL','tail':'API stream interrupted; partial usage retained by gateway'}
            return original(*args,**kwargs)
        self.engine.request_agent=request
        with patch('worker.pipeline.time.sleep'):
            self.execute()
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual(sum(n=='RequirementsCatalog' for n,_,_ in self.engine.requests),1)
        feedback=pipeline.read_json(self.out,'round-01/pipeline/task-graph/progress.json')['feedback']
        self.assertEqual(feedback['executor_error'],'Interrupted API stream')
        for text in ('HTTP 401 unauthorized','HTTP 429 budget exhausted','Provider API key is not configured','HTTP 503 invalid endpoint','Selected model is at capacity'):
            self.assertFalse(pipeline.transient_stream_failure({'status':'FAIL','tail':text+' API stream interrupted; partial usage retained by gateway'}))

    def test_transient_retry_cannot_escape_cumulative_allowance(self):
        from common.provider_settings import LITELLM_MODELS
        self.job['provider']='litellm';self.job['model']=next(iter(LITELLM_MODELS));self.job['dslab_max_calls']=1
        readings=iter([{'requests':0},{'requests':0},{'requests':1}])
        self.engine.request_agent=lambda *a,**k:{'status':'FAIL','tail':'API stream interrupted; partial usage retained by gateway'}
        with patch.object(pipeline.Cycle,'_usage',side_effect=lambda:next(readings)),patch('worker.pipeline.time.sleep'):
            cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
            with self.assertRaises(pipeline.AgentFailure) as caught:cycle.decomposition()
        self.assertIn('cumulative round',caught.exception.result['tail'])

    def test_snapshot_memory_count_refreshes_without_stage_publication(self):
        self.execute()
        from app import main as application
        saved=pipeline.read_json(self.out,'pipeline/public.json')['memory_summary']['record_count']
        pipeline_memory.append_event(self.out,self.ws,'validation',{'late':'failure diagnostic'},
            scope=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).scope,producer='controller',tags=['untested'])
        with patch.object(application,'pipeline_root',return_value=({'status':'USER_PAUSED','last_error':'stream'},self.out)):
            public=application.job_pipeline(self.job['id'])
        self.assertEqual(public['memory_summary']['record_count'],saved+1)
        self.assertEqual(public['status']['stage'],'paused')

    def test_api_reveals_first_release_accepted_checkpoint_as_draft(self):
        original=self.engine.request_agent
        def request(*args,**kwargs):
            if kwargs['output_contract']=='ReviewResult':raise pipeline.Interrupted('USER_PAUSED')
            return original(*args,**kwargs)
        self.engine.request_agent=request
        with self.assertRaises(pipeline.Interrupted):pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        # Emulate the first release: only complete.json retained the catalog.
        (self.out/'pipeline/draft.json').unlink()
        public=pipeline.read_json(self.out,'pipeline/public.json');public['catalog']=None;public['graph']=None
        pipeline.write_json(self.out,'pipeline/public.json',public)
        from app import main as application
        with patch.object(application,'pipeline_root',return_value=({'status':'USER_PAUSED','last_error':'stream'},self.out)):
            public=application.job_pipeline(self.job['id'])
        self.assertEqual(len(public['catalog']['conditions']),2)
        self.assertEqual(public['decomposition']['semantic_review'],'pending')
        self.assertFalse(public['status']['ready'])
        self.assertFalse((self.out/'pipeline/catalog.json').exists())

    def test_question_lifecycle_preserves_history_and_engineering_is_not_blocking(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        from common import task_graph
        catalog['open_questions']=['Which API method?', 'What should happen on genuine spec conflict?']
        resolution={'question':catalog['open_questions'][0],'kind':'implementation_decision','status':'resolved',
            'owner':'planner','condition_ids':['C1'],'answer':'Use the documented existing method',
            'reason':'Implementation choice permitted by the specification','source_refs':catalog['requirements'][0]['source_refs']}
        catalog['question_resolutions']=[resolution]
        self.assertEqual(task_graph.pending_questions(catalog),catalog['open_questions'][1:])
        with self.assertRaises(ValueError):cycle.validate_questions(catalog,lambda ref:pipeline.source_bytes(self.ws,ref))
        resolution=deepcopy(resolution);resolution.update(question=catalog['open_questions'][1],kind='user_clarification',status='open',owner='user',answer=None)
        catalog['question_resolutions'].append(resolution)
        cycle.validate_questions(catalog,lambda ref:pipeline.source_bytes(self.ws,ref))
        dropped=deepcopy(catalog);dropped['open_questions']=[];dropped['question_resolutions']=[]
        with self.assertRaises(ValueError):cycle.validate_questions(dropped,lambda ref:pipeline.source_bytes(self.ws,ref),catalog)

    def test_unresolved_experiment_updates_do_not_regenerate_reviewed_graph(self):
        self.engine.question_kind='experiment'
        for number in (1,2,3):
            pipeline.Cycle(self.engine,self.job,self.ws,self.out,number).decomposition()
        self.assertEqual(sum(n=='TaskGraph' for n,_,_ in self.engine.requests),1)
        self.assertEqual(sum(n=='ReviewResult' and c['input']['subject']=='decomposition' for n,_,c in self.engine.requests),1)

    def test_real_user_question_allows_coding_but_blocks_final_readiness(self):
        self.engine.question_kind='user_clarification';self.execute()
        self.assertTrue(any(n=='CoderReport' for n,_,_ in self.engine.requests))
        self.assertEqual(self.engine.updates[-1]['phase'],'spec_clarification_required')
        self.assertFalse(pipeline.read_json(self.out,'pipeline/public.json')['status']['ready'])
        before=len(self.engine.requests)
        pipeline.Cycle(self.engine,self.job,self.ws,self.out,2).decomposition()
        self.assertEqual(len(self.engine.requests),before,'An absent user answer cannot be obtained by re-planning')

    def test_recorded_engineering_choice_does_not_block_final_verification(self):
        self.engine.question_kind='implementation_decision';self.execute()
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual(self.engine.verifications,2)

    def test_coder_stream_interruption_continues_actual_source_and_original_assignment(self):
        original=self.engine.request_agent;attempts=[]
        def request(*args,**kwargs):
            context=json.loads(args[3].split('Input JSON:\n',1)[1])
            if kwargs['role']=='coder' and attempts:
                self.assertTrue((self.ws/'src/gametest/PlantTest.java').is_file())
                self.assertEqual(context['scope']['source_revision'],acceptance.source_fingerprint(self.ws))
                self.assertEqual(context['input']['assignment']['scope']['source_revision'],attempts[0])
            result=original(*args,**kwargs)
            if kwargs['role']=='coder' and not attempts:
                attempts.append(context['input']['assignment']['scope']['source_revision'])
                return {'status':'FAIL','tail':'API stream interrupted; partial usage retained by gateway'}
            return result
        self.engine.request_agent=request
        with patch('worker.pipeline.time.sleep'):self.execute()
        self.assertEqual(len(attempts),1)
        self.assertTrue((self.ws/'src/gametest/PlantTest.java').is_file())
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual(sum(n=='CoderReport' for n,_,_ in self.engine.requests),2)

    def test_token_exhaustion_alone_stops_transient_retry(self):
        from common.provider_settings import LITELLM_MODELS
        self.job.update(provider='litellm',model=LITELLM_MODELS[0],dslab_token_budget=100)
        readings=iter([{'input_tokens':0},{'input_tokens':0},{'input_tokens':100}]);calls=[]
        def request(*a,**k):
            calls.append(k);return {'status':'FAIL','tail':'API stream interrupted; partial usage retained by gateway'}
        self.engine.request_agent=request
        with patch.object(pipeline.Cycle,'_usage',side_effect=lambda:next(readings)),patch('worker.pipeline.time.sleep'):
            with self.assertRaises(pipeline.AgentFailure):pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        self.assertEqual(len(calls),1)

    def test_user_pause_between_stream_failure_and_retry_prevents_next_call(self):
        calls=[]
        def request(*a,**k):
            calls.append(k);self.engine.status='USER_PAUSED'
            return {'status':'FAIL','tail':'API stream interrupted; partial usage retained by gateway'}
        self.engine.request_agent=request
        with self.assertRaises(pipeline.Interrupted):pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        self.assertEqual(len(calls),1)

    def test_provider_retry_categories_preserve_causes_and_exclude_wrapped_client_errors(self):
        examples={
            'API request stopped (HTTP 502): Provider connection failed':'provider_unavailable',
            'API request stopped (HTTP 503): temporary overload':'provider_unavailable',
            'API turn incomplete (stop reason: error)':'provider_generation_error',
            'API request stopped (HTTP 502): Provider rejected request (HTTP 401)':None,
            'API request stopped (HTTP 502): Provider rejected request (HTTP 429)':None,
            'API request stopped (HTTP 503): Provider API key is not configured':None,
            'API turn incomplete: output limit reached (stop reason: max_tokens)':None,
        }
        for text,expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(pipeline.retry_category({'status':'FAIL','tail':text}),expected)

    def test_retry_uses_actual_terminal_error_not_words_in_untrusted_source(self):
        events=[{'type':'item.completed','item':{'type':'tool_result','text':
                 'GrowthBudget.java GrowthBudgetTest.java quota HTTP 429 invalid endpoint configuration'}},
                {'type':'turn.failed','error':{'message':'API request stopped (HTTP 502): Provider connection failed'}}]
        tail='truncated first transcript line\n'+'\n'.join(json.dumps(e) for e in events)
        self.assertEqual(pipeline.retry_category({'status':'FAIL','error':None,'tail':tail}),'provider_unavailable')
        self.assertEqual(pipeline.terminal_error({'tail':tail}),'API request stopped (HTTP 502): Provider connection failed')

    def test_quoted_provider_error_without_terminal_failure_cannot_trigger_retry(self):
        tail=json.dumps({'type':'item.completed','item':{'type':'tool_result',
            'text':json.dumps({'type':'turn.failed','error':{'message':'API request stopped (HTTP 502): Provider connection failed'}})}})
        self.assertIsNone(pipeline.retry_category({'status':'FAIL','tail':tail}))

    def test_latest_terminal_and_explicit_supervisor_error_take_precedence(self):
        tail='\n'.join(json.dumps({'type':'turn.failed','error':{'message':message}}) for message in (
            'API request stopped (HTTP 502): Provider connection failed',
            'API request stopped (HTTP 429): Insufficient remaining task token budget'))
        self.assertIsNone(pipeline.retry_category({'status':'FAIL','tail':tail}))
        self.assertEqual(pipeline.retry_category({'status':'FAIL','tail':tail,
            'error':'API request stopped (HTTP 502): Provider connection failed'}),'provider_unavailable')

    def test_real_transcript_502_with_growth_budget_source_retries_same_phase(self):
        original=self.engine.request_agent;fail=[True];calls=[]
        def request(*args,**kwargs):
            if kwargs['output_contract']=='ReviewResult':
                calls.append(1)
                if fail:
                    fail.pop()
                    tail='\n'.join(json.dumps(e) for e in [
                        {'type':'item.completed','item':{'type':'tool_result','text':'GrowthBudget.java: class GrowthBudget {}'}},
                        {'type':'turn.failed','error':{'message':'API request stopped (HTTP 502): Provider connection failed'}}])
                    return {'status':'FAIL','error':None,'tail':tail}
            return original(*args,**kwargs)
        self.engine.request_agent=request
        with patch('worker.pipeline.time.sleep'):self.execute()
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual(len(calls),5)

    def test_provider_502_and_generation_error_retry_same_phase_with_backoff(self):
        original=self.engine.request_agent;failures=[
            'API request stopped (HTTP 502): Provider connection failed',
            'API turn incomplete (stop reason: error)'];calls=[]
        def request(*args,**kwargs):
            if kwargs['output_contract']=='TaskGraph':
                calls.append(kwargs)
                if failures:return {'status':'FAIL','tail':failures.pop(0)}
            return original(*args,**kwargs)
        self.engine.request_agent=request
        with patch('worker.pipeline.time.sleep') as sleep:self.execute()
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual(len(calls),3)
        self.assertEqual(sleep.call_count,6)
        self.assertEqual(sum(n=='RequirementsCatalog' for n,_,_ in self.engine.requests),1)
        progress=pipeline.read_json(self.out,'round-01/pipeline/task-graph/progress.json')
        self.assertEqual(progress['feedback']['retry_category'],'provider_generation_error')
        self.assertEqual(progress['feedback']['retry_delay_seconds'],4)

    def test_pause_during_provider_backoff_prevents_next_request(self):
        attempts=[]
        def request(*a,**k):
            attempts.append(k);return {'status':'FAIL','tail':'API request stopped (HTTP 502): Provider connection failed'}
        self.engine.request_agent=request
        with patch('worker.pipeline.time.sleep',side_effect=lambda _:setattr(self.engine,'status','USER_PAUSED')):
            with self.assertRaises(pipeline.Interrupted):pipeline.Cycle(self.engine,self.job,self.ws,self.out,1).decomposition()
        self.assertEqual(len(attempts),1)

    def test_retry_wait_does_not_apply_one_agent_timeout_to_entire_cycle(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        cycle.started-=self.engine.AGENT_TIMEOUT+1
        with patch('worker.pipeline.time.sleep'):cycle.retry_wait('planner',2)
        self.assertEqual(cycle.request_seconds('planner'),self.engine.AGENT_TIMEOUT)

    def test_writer_continuation_survives_controller_reconstruction_after_pause(self):
        original=self.engine.request_agent;first=[True];base=[]
        def request(*args,**kwargs):
            context=json.loads(args[3].split('Input JSON:\n',1)[1])
            if kwargs['role']=='coder' and not first:
                self.assertTrue((self.ws/'src/gametest/PlantTest.java').is_file())
                self.assertEqual(context['scope']['source_revision'],acceptance.source_fingerprint(self.ws))
                self.assertEqual(context['input']['assignment']['scope']['source_revision'],base[0])
            result=original(*args,**kwargs)
            if kwargs['role']=='coder' and first:
                first.pop();base.append(context['input']['assignment']['scope']['source_revision'])
                self.engine.status='USER_PAUSED'
                return {'status':'FAIL','tail':'API stream interrupted; partial usage retained by gateway'}
            return result
        self.engine.request_agent=request;self.execute()
        self.assertEqual(self.engine.updates[-1]['status'],'USER_PAUSED')
        self.assertTrue((self.ws/'src/gametest/PlantTest.java').is_file())
        self.engine.status=None;self.execute()
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual(sum(n=='CoderReport' for n,_,_ in self.engine.requests),2)

    def test_provider_failure_is_archived_and_resume_makes_a_fresh_attempt(self):
        original=self.engine.request_agent;fail=[True]
        def request(*args,**kwargs):
            if kwargs['output_contract']=='PlannerDecision' and fail:
                fail.pop();return {'status':'FAIL','provider':'codex','role':'planner','tail':'Selected model unavailable'}
            return original(*args,**kwargs)
        self.engine.request_agent=request
        self.execute()
        self.assertEqual(self.engine.updates[-1]['status'],'USER_PAUSED')
        progress=json.loads((self.out/'round-01/pipeline/plan/progress.json').read_text())
        self.assertEqual(progress['attempt'],2)
        self.execute()
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')


if __name__=='__main__':unittest.main()

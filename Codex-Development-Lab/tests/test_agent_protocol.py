"""Compact messages -> canonical history -> reviewer -> trusted verifier.

These are protocol fixtures, never live model coding-quality measurements.
"""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from common import agent_protocol as protocol, pipeline_contracts as contracts, preparation
from worker import pipeline
from agent import dslab,daemon
from tests import test_pipeline_cycle as legacy_cycle


class AgentProtocolTests(unittest.TestCase):
    def test_verification_memory_indexes_overlapping_actions_without_losing_payloads(self):
        from common import pipeline_memory
        self.install_wire_model();cycle=self.cycle()
        catalog,graph=cycle.decomposition()
        decision,graph=cycle.plan(catalog,graph,{})
        report,manifest,review=cycle.code_and_review(catalog,graph,decision)
        decision=deepcopy(decision)
        repeated=deepcopy(decision['actions'][0]);repeated['id']='A2'
        decision['actions'].append(repeated)
        original=deepcopy(decision)
        plan_record=cycle.event('plan',decision,'planner')
        result,legacy=self.fixture.verified(self.ws,cycle.stage,self.job['id'],catalog,manifest,decision)
        original_result=deepcopy(result)
        cycle.record_verification(catalog,manifest,decision,result,legacy)
        record=pipeline_memory.load_record(self.out,cycle.state['last_verification_record'])
        self.assertEqual(record['entry']['task_ids'],list(dict.fromkeys(
            t for a in decision['actions'] for t in a['task_ids'])))
        self.assertEqual(pipeline_memory.read_payload(self.out,record['record_id']),original_result)
        self.assertEqual(pipeline_memory.read_payload(self.out,plan_record['record_id']),original)
        self.assertEqual(decision,original)
        self.assertTrue(cycle.coverage(catalog))
        self.assertTrue((cycle.stage/'verification_complete.json').is_file())

    def test_capture_only_wait_session_replans_without_resetting_sources(self):
        from common import evidence_policy
        self.install_wire_model();cycle=self.cycle();catalog,graph=cycle.decomposition()
        capture={**deepcopy(catalog['conditions'][0]),'id':'CAPTURE','expected':'Attach optional recording'}
        catalog['conditions'].append(capture);catalog['requirements'][0]['condition_ids'].append('CAPTURE')
        graph['tasks'][0]['condition_ids'].append('CAPTURE')
        handoff=preparation.route_recorded_questions(cycle.scope,graph,catalog,
            pipeline.export_json(self.ws,'capture-catalog.json',catalog),pipeline.export_json(self.ws,'capture-graph.json',graph),[])
        handoff['tasks'][0].update(status='needs_user',reason='Need recording duration',return_when='User chooses duration')
        cycle.state['preparation_handoff']=handoff;cycle.save()
        decision=contracts.load_example('PlannerDecision');decision.update(scope=deepcopy(cycle.scope),strategy='wait',actions=[],rationale='Need recording duration')
        pipeline.write_json(cycle.stage,'pipeline/session.json',{'scope':cycle.scope,'catalog':catalog,'graph':graph,'decision':decision})
        pipeline.write_json(self.out,'pipeline/evidence-policy.json',{'spec_revision':catalog['scope']['spec_revision'],
            'supplemental_conditions':{'CAPTURE':evidence_policy.definition(capture)},'release_task_ids':['T1']})
        before=(self.ws/'existing.java').read_text()
        with patch('worker.pipeline_verification.verify',side_effect=self.fixture.verified):
            pipeline.run(self.e,self.job,self.ws,self.out,{})
        self.assertGreater(self.e.verifications,0)
        self.assertEqual((self.ws/'existing.java').read_text(),before)
        self.assertFalse(any(row.get('phase')=='pipeline_waiting' for row in self.e.updates))
        self.assertTrue(pipeline.read_json(self.out,'pipeline/state.json')['supplemental_evidence'])

    def test_action_dependency_error_repairs_plan_without_rewriting_valid_graph(self):
        self.install_wire_model();original=self.e.request_agent;plan_count=0;calls=[]
        def request(*args,**kwargs):
            nonlocal plan_count
            result=original(*args,**kwargs);name=kwargs['output_contract'];calls.append(name)
            if name=='AgentPlan':
                plan_count+=1;p=args[4];rows=[json.loads(s) for s in p.read_text(encoding='utf-8').splitlines()]
                value=json.loads(rows[-1]['item']['text'])
                if plan_count==1:
                    value['request_graph_change']=True
                    value['actions'][0].update(task_ids=['T2'],condition_ids=['C2'])
                rows[-1]['item']['text']=json.dumps(value)
                p.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            return result
        self.e.request_agent=request
        cycle=self.cycle();catalog,graph=cycle.decomposition()
        before=calls.count('AgentGraph')
        decision,updated=cycle.plan(catalog,graph,{})
        self.assertEqual(decision['actions'][0]['task_ids'],['T1','T2'])
        self.assertEqual(calls.count('AgentGraph')-before,1)
        self.assertEqual(calls.count('AgentPlan'),2)
        self.assertEqual(updated['tasks'][1]['depends_on'],['T1'])
        self.assertEqual(self.e.verifications,0)
        self.assertFalse(any(name=='AgentReport' for name in calls))

    def setUp(self):
        self.fixture=legacy_cycle.CycleTest(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.e=self.fixture.engine;self.e.COMPACT_PROTOCOL=True
        self.ws=self.fixture.ws;self.out=self.fixture.out;self.job=self.fixture.job

    def cycle(self):return pipeline.Cycle(self.e,self.job,self.ws,self.out,1)

    def install_wire_model(self):
        original=self.e.request_agent;calls=[]
        def request(*args,**kwargs):
            name=kwargs['output_contract'];calls.append(name)
            canonical=protocol.TARGETS.get(name,name)
            result=original(*args,**{**kwargs,'output_contract':canonical})
            if name in protocol.TARGETS:
                log=args[4];events=[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
                value=json.loads(events[-1]['item']['text']);wire=protocol.to_wire(value)
                if name=='AgentReport':
                    path=self.ws/wire['scenario_manifest_path']
                    manifest=protocol.to_wire(json.loads(path.read_text(encoding='utf-8')))
                    manifest['notes_md']='Scenario details must survive normalization.\n\nNo runtime result is claimed.'
                    path.write_text(json.dumps(manifest),encoding='utf-8')
                wire['notes_md']='## Decision\n\nComplete explanation.\n\nHypothesis: inspect before claiming success.\n\n<script>untrusted</script>'
                events[-1]['item']['text']=json.dumps(wire)
                log.write_text('\n'.join(json.dumps(event) for event in events),encoding='utf-8')
            return result
        self.e.request_agent=request
        return calls

    def test_compact_end_to_end_keeps_full_notes_metadata_and_independent_acceptance(self):
        calls=self.install_wire_model()
        with patch('worker.pipeline_verification.verify',side_effect=self.fixture.verified):
            pipeline.run(self.e,self.job,self.ws,self.out,{})
        self.assertEqual(calls,['AgentCatalog','AgentReview','AgentGraph','AgentReview','AgentPlan','AgentReport','AgentReview','AgentReview'])
        self.assertEqual(self.e.verifications,2)
        public=pipeline.read_json(self.out,'pipeline/public.json')
        self.assertTrue(public['status']['ready'])
        for role in ('planner','coder','reviewer'):
            saved=public['agent_reports'][role]
            raw=pipeline.source_bytes(self.ws,saved['report_ref']).decode()
            self.assertEqual(raw,saved['notes_md'])
            self.assertIn('<script>untrusted</script>',raw)
        report=pipeline.read_json(self.ws,'.lab-pipeline/coder-report.json')
        self.assertTrue(any(file['path']=='src/gametest/PlantTest.java' for file in report['changed_files']))
        self.assertEqual(report['action_ids'],['A1'])
        contracts.validate('CoderReport',report)
        from common import pipeline_memory
        index=pipeline.read_json(self.out,'pipeline-memory/index.json')
        payloads=[pipeline_memory.read_payload(self.out,e['record_id']) for e in index['records']]
        messages=[p for p in payloads if 'wire_message' in p]
        self.assertEqual(len(messages),9)
        self.assertTrue(all('scope' not in p['wire_message'] for p in messages))
        scenario=next(p for p in messages if p['wire_message']['contract']=='AgentScenarios')
        self.assertIn('Scenario details must survive normalization.',scenario['report_md'])
        self.assertIn('No runtime result is claimed.',scenario['wire_message']['notes_md'])

    def test_handoff_routes_existing_questions_without_model_classification(self):
        catalog=contracts.load_example('RequirementsCatalog');graph=contracts.load_example('TaskGraph')
        catalog['scope']=deepcopy(self.cycle().scope);graph['scope']=deepcopy(catalog['scope'])
        catalog['open_questions']=['Observe external API']
        catalog['question_resolutions']=[{'question':'Observe external API','kind':'experiment','status':'open',
            'owner':'coder','condition_ids':['C1'],'answer':None,'reason':'Actual uncertainty',
            'source_refs':catalog['requirements'][0]['source_refs']}]
        cycle=self.cycle();cycle.ensure_handoff(catalog,graph)
        self.assertEqual(self.e.requests,[])
        handoff=cycle.state['preparation_handoff']
        self.assertEqual(handoff['tasks'][0]['status'],'experiment')
        self.assertEqual(handoff['tasks'][1]['status'],'ready')
        self.assertIn('Observe external API',handoff['tasks'][0]['experiment'])

    def test_external_dependencies_are_preserved_and_can_be_explicitly_resolved(self):
        catalog=contracts.load_example('RequirementsCatalog');graph=contracts.load_example('TaskGraph')
        cycle=self.cycle();ref=pipeline.export_json(self.ws,'catalog.json',catalog);gref=pipeline.export_json(self.ws,'graph.json',graph)
        old=preparation.route_recorded_questions(cycle.scope,graph,catalog,ref,gref,[])
        for row in old['tasks']:row.update(status='external',reason='Waiting for external access',return_when='Access restored')
        routed=preparation.route_recorded_questions(cycle.scope,graph,catalog,ref,gref,[],old)
        self.assertTrue(all(t['status']=='external' for t in routed['tasks']))
        updates=[{'task_id':t['task_id'],'state':'resolved','reason':'Observed external access restored','return_when':'Ready to select'} for t in old['tasks']]
        updated=preparation.update_external(routed,updates,graph,catalog)
        self.assertTrue(all(t['status']=='ready' for t in updated['tasks']))
        self.assertTrue(all(t['status']=='external' for t in old['tasks']))

    def test_generated_wire_artifacts_match_their_maintained_codec(self):
        for wire,target in protocol.TARGETS.items():
            with self.subTest(wire=wire):
                self.assertEqual(contracts.load_schema(wire),protocol.compact_schema(wire,contracts.load_schema(target)))
                self.assertEqual(contracts.load_example(wire),protocol.to_wire(contracts.load_example(target)))

    def test_coder_review_responses_keep_full_explanations_and_real_references(self):
        self.install_wire_model()
        cycle=self.cycle();catalog,graph=cycle.decomposition();decision,graph=cycle.plan(catalog,graph,{})
        report,manifest,review=cycle.code_and_review(catalog,graph,decision)
        assignment={'scope':cycle.scope,'plan_ref':report['plan_ref']}
        finding=contracts.load_example('ReviewResult')['findings'][0]
        review['findings']=[finding]
        wire={'contract':'AgentReport','version':'v1','scenario_manifest_path':report['scenario_manifest_ref']['path'],
            'notes_md':'Full code report','responses':[{'finding_id':finding['id'],'disposition':'unverified',
                'notes_md':'Changed implementation.\n\nStill needs an independent world observation.'}],
            'unverified_claims':['No gameplay success established'],'open_issues':['Need independent execution']}
        result=protocol.hydrate(wire,scope=cycle.scope,inputs={'assignment':assignment,'review':review},
            artifact=lambda path:pipeline.artifact(self.ws,path),read=lambda ref:pipeline.source_bytes(self.ws,ref),
            write=lambda file,obj:pipeline.export_json(self.ws,file,obj))
        response=json.loads(pipeline.source_bytes(self.ws,result['review_response_refs'][0]))
        self.assertEqual(response['responses'][0]['explanation'],wire['responses'][0]['notes_md'])
        self.assertEqual(response['responses'][0]['disposition'],'unverified')
        self.assertEqual(result['scope'],cycle.scope)
        self.assertEqual(result['open_issues'],wire['open_issues'])
        contracts.validate('ReviewResponse',response)

    def test_legacy_citation_provenance_becomes_advisory_but_ids_and_coverage_stay_checked(self):
        cycle=self.cycle()
        cycle.citation_advice([{'artifact':{'path':'not-read.java','sha256':'a'*64,'media_type':'text/plain'},
                               'start_line':1,'end_line':1,'excerpt':'not observed'}],'','test')
        self.assertTrue(cycle.state['events'])
        # Schema still rejects unknown fields/types; graph validation remains.
        from common import task_graph
        value=contracts.load_example('PlannerDecision');graph=contracts.load_example('TaskGraph');catalog=contracts.load_example('RequirementsCatalog')
        value['actions'][0]['task_ids']=['T1-A']
        with self.assertRaises(ValueError):task_graph.validate_decision(value,graph,catalog)

    def test_native_and_cli_use_same_small_schema_and_zero_owned_fields(self):
        value=contracts.load_example('AgentPlan');requests=[]
        def send(request,*args,**kwargs):
            requests.append(request)
            return [{'type':'tool_use','id':'plan','name':'submit_result','input':value}],'tool_use'
        task={'role':'planner','provider':'minimax','model':'fixture','dslab_capability':'synthetic',
              'dslab_max_calls':10,'output_contract':'AgentPlan'}
        with redirect_stdout(io.StringIO()):
            result=dslab.Executor(task,self.ws,send=send).run('Input JSON:\n{}',agent='planner',readonly=True)
        self.assertEqual(json.loads(result),value)
        schema=next(t['input_schema'] for t in requests[0]['tools'] if t['name']=='submit_result')
        self.assertEqual(schema,contracts.load_submission_schema('AgentPlan'))
        self.assertNotIn('scope',schema['properties'])
        self.assertNotIn('hypotheses',schema['properties'])
        with patch.object(daemon,'EXECUTOR_ROLE','planner'):
            command=daemon.build_command({**task,'provider':'codex','model':''},self.ws)
        self.assertTrue(command[command.index('--output-schema')+1].endswith('AgentPlan.submission.schema.json'))

    def test_plain_markdown_is_not_a_json_format_failure(self):
        value=contracts.load_example('AgentPlan');value['notes_md']=''
        requests=[]
        def send(request,*args,**kwargs):
            requests.append(deepcopy(request))
            if len(requests)==1:return [{'type':'text','text':'[Build log](build.log)\n\n[!NOTE]\n\nRead the current failure. Try a different experiment.'}],'end_turn'
            return [{'type':'tool_use','id':'plan','name':'submit_result','input':value}],'tool_use'
        task={'role':'planner','provider':'minimax','model':'fixture','dslab_capability':'synthetic',
              'dslab_max_calls':10,'output_contract':'AgentPlan'}
        with redirect_stdout(io.StringIO()) as output:
            actual=dslab.Executor(task,self.ws,send=send).run('Input JSON:\n{}',agent='planner',readonly=True)
        self.assertEqual(json.loads(actual),value)
        self.assertEqual(requests[1]['tool_choice'],{'type':'tool','name':'submit_result'})
        self.assertNotIn('correction.record',output.getvalue())

    def test_prose_outside_machine_json_is_archived_without_interpreting_it(self):
        self.install_wire_model();original=self.e.request_agent
        text='## Full public report\n\n"Quotes", JSON examples, and free Markdown.\n\nNo PASS claimed.'
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='AgentPlan':
                p=args[4];rows=[json.loads(s) for s in p.read_text(encoding='utf-8').splitlines()]
                value=json.loads(rows[-1]['item']['text']);value['notes_md']=''
                rows[-1]['item']['text']=json.dumps(value)
                rows.insert(-1,{'type':'item.completed','item':{'type':'agent_message','agent_id':'planner','text':text}})
                p.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            return result
        self.e.request_agent=request
        cycle=self.cycle();catalog,graph=cycle.decomposition();decision,_=cycle.plan(catalog,graph,{})
        self.assertEqual(decision['rationale'],text)
        report=cycle.state['agent_reports']['planner']
        self.assertIn(text,pipeline.source_bytes(self.ws,report['report_ref']).decode())

    def test_old_complete_and_old_point_repair_remain_resumable_after_upgrade(self):
        self.e.COMPACT_PROTOCOL=False;cycle=self.cycle()
        inputs={'scope':cycle.scope}
        accepted=cycle.ask('PlannerDecision','planner',inputs,'accepted-legacy')
        count=len(self.e.requests)
        self.e.COMPACT_PROTOCOL=True;cycle=self.cycle()
        reused=cycle.ask('PlannerDecision','planner',inputs,'accepted-legacy')
        self.assertEqual(reused,accepted)
        self.assertEqual(len(self.e.requests),count)
        self.e.COMPACT_PROTOCOL=False;self.e.invalid_plans=1
        original=self.e.request_agent
        def interrupted(*args,**kwargs):
            if kwargs['output_contract']=='SubmissionPatch':raise pipeline.Interrupted('fixture pause')
            return original(*args,**kwargs)
        self.e.request_agent=interrupted
        with self.assertRaises(pipeline.Interrupted):cycle.ask('PlannerDecision','planner',inputs,'repair-legacy')
        self.e.COMPACT_PROTOCOL=True;self.e.request_agent=original;count=len(self.e.requests)
        resumed=self.cycle().ask('PlannerDecision','planner',inputs,'repair-legacy')
        contracts.validate('PlannerDecision',resumed)
        self.assertEqual(len(self.e.requests),count+1)
        self.assertEqual(self.e.requests[-1][0],'SubmissionPatch')

    def test_research_implementation_reaches_runner_but_open_question_cannot_finish_project(self):
        self.e.question_kind='experiment'
        self.job['last_error']='Configured cycle execution time elapsed; checkpoints preserved'
        calls=self.install_wire_model()
        with patch('worker.pipeline_verification.verify',side_effect=self.fixture.verified):
            pipeline.run(self.e,self.job,self.ws,self.out,{})
        self.assertIn('AgentReport',calls)
        self.assertEqual(self.e.verifications,1)
        public=pipeline.read_json(self.out,'pipeline/public.json')
        self.assertFalse(public['status']['ready'])
        self.assertTrue(any(q['status']=='open' and q['kind']=='experiment' for q in public['catalog']['question_resolutions']))
        self.assertTrue(any(t['status']=='experiment' for t in public['handoff']['tasks']))
        self.assertFalse(any(update.get('phase')=='acceptance_verified' for update in self.e.updates))

    def test_saved_plan_rejected_only_by_retired_gate_is_revalidated_without_model(self):
        cycle=self.cycle();inputs={'scope':cycle.scope};label='retired-research-plan'
        value=contracts.load_example('PlannerDecision');value['scope']=deepcopy(cycle.scope)
        ref=pipeline.export_json(self.ws,'old-research-plan.json',value)
        issue={'path':'$','code':'semantic_links',
            'expected':'Unresolved technical experiment requires diagnose before implementation; update handoff after real results',
            'received_type':'object','example_json':'{}','instruction':'Old rule.'}
        folder=cycle.stage/'pipeline'/label
        folder.mkdir(parents=True,exist_ok=True)
        pipeline.write_json(folder,'progress.json',{'attempt':3,'input_binding':pipeline.input_binding(inputs),
            'feedback':{'issues':[issue],'previous_output_ref':ref}})
        with (patch.object(self.e,'request_agent',side_effect=AssertionError('Paid replanning')),
                patch.object(cycle,'validation_result',wraps=cycle.validation_result) as validate):
            actual=cycle.ask('PlannerDecision','planner',inputs,label,validate_extra=lambda v,o:contracts.validate('PlannerDecision',v))
        self.assertEqual(actual,value)
        self.assertTrue((folder/'complete.json').is_file())
        self.assertTrue(validate.call_args.args[2])
        self.assertEqual(cycle.coverage(contracts.load_example('RequirementsCatalog'))[0]['status'],'untested')

    def test_migration_keeps_actual_errors_and_removes_retired_issue_from_point_repair(self):
        cycle=self.cycle();inputs={'scope':cycle.scope};label='mixed-research-repair'
        value=contracts.load_example('PlannerDecision');value['scope']=deepcopy(cycle.scope)
        value['actions'][0]['task_ids']=['T1-A']
        base=pipeline.export_json(self.ws,'old-mixed-plan.json',value)
        retired={'path':'$','code':'semantic_links','expected':'Unresolved technical experiment requires diagnose before implementation'}
        actual={'path':'$.actions[0].task_ids','code':'unknown_task','expected':'Use actual graph IDs'}
        folder=cycle.stage/'pipeline'/label
        folder.mkdir(parents=True,exist_ok=True)
        pipeline.write_json(folder,'progress.json',{'attempt':3,'input_binding':pipeline.input_binding(inputs),
            'feedback':{'issues':[retired,actual],'previous_output_ref':base},
            'repair':{'base_ref':base,'issues':[retired,actual],'target_contract':'PlannerDecision',
                'source_revision':cycle.scope['source_revision'],
                'observed_ref':pipeline.export_json(self.ws,'old-observed.json',{'text':''})}})
        requests=[]
        def capture(*args,**kwargs):
            requests.append((json.loads(args[3].split('Input JSON:\n',1)[1]),kwargs['output_contract']))
            raise pipeline.Interrupted('fixture pause')
        with patch.object(self.e,'request_agent',side_effect=capture),self.assertRaises(pipeline.Interrupted):
            cycle.ask('PlannerDecision','planner',inputs,label)
        context,name=requests[0]
        self.assertEqual(name,'SubmissionPatch')
        self.assertEqual(context['input']['issues'],[actual])
        self.assertEqual(context['input']['allowed_paths'],['$.actions[0].task_ids'])
        self.assertFalse((folder/'complete.json').exists())

    def test_recovered_compact_plan_commits_its_original_external_updates(self):
        from common import correction_journal
        cycle=self.cycle();label='recover-external-research'
        catalog=contracts.load_example('RequirementsCatalog');catalog['scope']=deepcopy(cycle.scope)
        graph=contracts.load_example('TaskGraph');graph['scope']=deepcopy(cycle.scope)
        cref=pipeline.export_json(self.ws,'external-catalog.json',catalog)
        gref=pipeline.export_json(self.ws,'external-graph.json',graph)
        inputs={'catalog_ref':cref,'graph_ref':gref}
        handoff=preparation.route_recorded_questions(cycle.scope,graph,catalog,cref,gref,[])
        handoff['tasks'][0].update(status='experiment',experiment='Inspect API')
        cycle.state['preparation_handoff']=handoff
        canonical=contracts.load_example('PlannerDecision');canonical['scope']=deepcopy(cycle.scope)
        wire=protocol.to_wire(canonical)
        wire['external_updates']=[{'task_id':'T2','state':'blocked','reason':'External credentials missing','return_when':'User supplies external access'}]
        canonical=protocol.hydrate(wire,scope=cycle.scope,inputs=inputs,
            artifact=lambda path:pipeline.artifact(self.ws,path),read=lambda ref:pipeline.source_bytes(self.ws,ref),
            write=lambda file,obj:pipeline.export_json(self.ws,file,obj))
        issue={'path':'$','code':'semantic_links','expected':'Unresolved technical experiment requires diagnose before implementation'}
        phase=label+'@'+pipeline.digest({'round':cycle.number,'input_binding':pipeline.input_binding(inputs)})
        record=correction_journal.record('PlannerDecision',wire,[issue],layer='controller_validation')
        cycle.store_correction(record,phase)
        folder=cycle.stage/'pipeline'/label;folder.mkdir(parents=True,exist_ok=True)
        pipeline.write_json(folder,'progress.json',{'attempt':2,'input_binding':pipeline.input_binding(inputs),
            'feedback':{'issues':[issue],'correction_id':record['correction_id'],
                'previous_output_ref':pipeline.export_json(self.ws,'external-old-plan.json',canonical)}})
        def validate(value,observed):
            effective=preparation.update_external(handoff,cycle._pending_external_updates,graph,catalog)
            preparation.validate_package(value,effective,graph,catalog)
        with patch.object(self.e,'request_agent',side_effect=AssertionError('Unnecessary replanning')):
            actual=cycle.ask('PlannerDecision','planner',inputs,label,validate_extra=validate)
        self.assertEqual(actual,canonical)
        rows={row['task_id']:row for row in cycle.state['preparation_handoff']['tasks']}
        self.assertEqual(rows['T2']['status'],'external')
        self.assertEqual(rows['T2']['reason'],'External credentials missing')

    def test_older_canonical_correction_does_not_silently_lose_unrecoverable_updates(self):
        from common import correction_journal
        cycle=self.cycle();inputs={'scope':cycle.scope};label='unproven-wire-recovery'
        value=contracts.load_example('PlannerDecision');value['scope']=deepcopy(cycle.scope)
        issue={'path':'$','code':'semantic_links','expected':'Unresolved technical experiment requires diagnose before implementation'}
        phase=label+'@'+pipeline.digest({'round':cycle.number,'input_binding':pipeline.input_binding(inputs)})
        record=correction_journal.record('PlannerDecision',value,[issue],layer='controller_validation')
        cycle.store_correction(record,phase)
        folder=cycle.stage/'pipeline'/label;folder.mkdir(parents=True,exist_ok=True)
        pipeline.write_json(folder,'progress.json',{'attempt':2,'input_binding':pipeline.input_binding(inputs),
            'feedback':{'issues':[issue],'correction_id':record['correction_id'],
                'previous_output_ref':pipeline.export_json(self.ws,'unproven-old-plan.json',value)}})
        captured=[]
        def request(*args,**kwargs):
            captured.append(json.loads(args[3].split('Input JSON:\n',1)[1]))
            raise pipeline.Interrupted('fixture pause')
        with patch.object(self.e,'request_agent',side_effect=request),self.assertRaises(pipeline.Interrupted):
            cycle.ask('PlannerDecision','planner',inputs,label)
        self.assertFalse((folder/'complete.json').exists())
        self.assertIn('external updates',captured[0]['correction']['recovery_issue'])

    def test_compact_planning_routes_user_dependencies_before_scheduling(self):
        self.e.question_kind='user_clarification';self.install_wire_model()
        original=self.e.request_agent
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='AgentPlan':
                path=args[4];rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                value=json.loads(rows[-1]['item']['text']);value.update(strategy='wait',actions=[])
                rows[-1]['item']['text']=json.dumps(value)
                path.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            return result
        self.e.request_agent=request
        cycle=self.cycle();catalog,graph=cycle.decomposition()
        self.assertIsNone(cycle.state.get('preparation_handoff'))
        decision,_=cycle.plan(catalog,graph,{})
        self.assertEqual(decision['strategy'],'wait')
        blocked=preparation.blocked_tasks(cycle.state['preparation_handoff'],graph,catalog)
        self.assertEqual(set(blocked),{'T1','T2'})

    def test_new_wire_semantic_id_error_is_repaired_without_full_replanning(self):
        self.install_wire_model();original=self.e.request_agent
        calls=[]
        def request(*args,**kwargs):
            calls.append(kwargs['output_contract'])
            if kwargs['output_contract']=='SubmissionPatch':
                context=json.loads(args[3].split('Input JSON:\n',1)[1])
                value={'contract':'SubmissionPatch','version':'v1','submission_sha256':context['input']['submission_sha256'],
                    'changes':[{'path':'$.actions[0].task_ids','operation':'set','expected_exists':True,
                        'expected_old_json':'["T1-A"]','new_json':'["T1","T2"]'}]}
                p=args[4];p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','agent_id':'planner','text':json.dumps(value)}}),encoding='utf-8')
                return {'status':'PASS','role':'planner'}
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='AgentPlan':
                p=args[4];rows=[json.loads(s) for s in p.read_text(encoding='utf-8').splitlines()]
                value=json.loads(rows[-1]['item']['text']);value['actions'][0]['task_ids']=['T1-A']
                rows[-1]['item']['text']=json.dumps(value)
                p.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            return result
        self.e.request_agent=request
        cycle=self.cycle();catalog,graph=cycle.decomposition()
        decision,_=cycle.plan(catalog,graph,{})
        self.assertEqual(decision['actions'][0]['task_ids'],['T1','T2'])
        self.assertEqual(calls.count('AgentPlan'),1)
        self.assertEqual(calls.count('SubmissionPatch'),1)


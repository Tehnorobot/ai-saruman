"""Native role/output contracts, with no external provider calls."""
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock

from agent import daemon,dslab
from common.pipeline_contracts import load_example
from common.correction_journal import select


def field_patch(request,target):
    context=json.loads(request['messages'][0]['content'])
    return {'contract':'SubmissionPatch','version':'v1','submission_sha256':context['submission_sha256'],
        'changes':[{'path':field['path'],'operation':'set','expected_exists':field['exists'],
            'expected_old_json':json.dumps(field['saved_value']['value']),
            'new_json':json.dumps(select(target,field['path']))} for field in context['fields']]}


class PipelineExecutorTests(unittest.TestCase):
    def test_api_adapter_forwards_large_limits_without_old_1000_or_output_cap(self):
        example=load_example('ReviewResult');requests=[]
        def send(request,*args,**kwargs):
            requests.append(request)
            return [{'type':'tool_use','id':'ok','name':'submit_result','input':example}],'tool_use'
        task={**self.task,'dslab_max_calls':50000,'dslab_max_output_tokens':131072}
        with redirect_stdout(io.StringIO()):
            result=json.loads(dslab.Executor(task,self.root,send=send).run('Review',agent='reviewer',readonly=True))
        self.assertEqual(result,example)
        self.assertEqual(requests[0]['max_tokens'],131072)

    def test_controller_patch_executor_has_only_saved_field_access_and_preserves_role(self):
        from common import submission_patch
        from worker.pipeline import export_json
        base={'contract':'PreparationHandoff','scope':{'round':'12'},'tasks':[]}
        ref=export_json(self.root,'repair-base-fixture.json',base)
        patch_value={'contract':'SubmissionPatch','version':'v1','submission_sha256':submission_patch.fingerprint(base),
            'changes':[{'path':'$.scope.round','operation':'set','expected_exists':True,'expected_old_json':'"12"','new_json':'12'}]}
        task={**self.task,'role':'planner','output_contract':'SubmissionPatch'}
        context={'scope':{},'input':{**submission_patch.context(base,[{'path':'$.scope.round'}]),
            'base_ref':ref,'target_contract':'PreparationHandoff'}}
        requests=[]
        def send(request,*args,**kwargs):
            requests.append(copy.deepcopy(request))
            return [{'type':'tool_use','id':'patch','name':'submit_result','input':patch_value}],'tool_use'
        executor=dslab.Executor(task,self.root,send=send)
        with redirect_stdout(io.StringIO()):
            actual=json.loads(executor.run('Input JSON:\n'+json.dumps(context),agent='planner',readonly=True))
        self.assertEqual(actual,patch_value)
        self.assertEqual({tool['name'] for tool in requests[0]['tools']},{'submit_result','read_patch_field','read_output_format'})
        with self.assertRaises(ValueError):executor.tool('read_file',{'path':'src.java'},agent='planner',readonly=True)
        with patch.object(daemon,'EXECUTOR_ROLE','planner'),patch.object(daemon,'PROVIDER','any'):
            self.assertTrue(daemon.matches_executor({**task,'role':'coder','provider':'minimax'}))
        with patch.object(daemon,'EXECUTOR_ROLE','coder'),patch.object(daemon,'PROVIDER','dslab'):
            self.assertFalse(daemon.matches_executor({**task,'role':'coder','provider':'minimax'}))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.task={'role':'reviewer','provider':'litellm','model':'fixture','output_contract':'ReviewResult',
                   'dslab_max_calls':10,'dslab_max_output_tokens':8192,'dslab_capability':'synthetic-not-a-real-key'}

    def test_native_schema_is_an_object_and_three_format_corrections_are_allowed(self):
        example=load_example('ReviewResult');bad=copy.deepcopy(example);bad['reviewed_condition_ids']='C1'
        requests=[]
        def send(request,*args,**kwargs):
            requests.append(copy.deepcopy(request))
            output=bad if len(requests)==1 else field_patch(request,bad if len(requests)<4 else example)
            return [{'type':'tool_use','id':'s'+str(len(requests)),'name':'submit_result' if len(requests)==1 else 'submit_patch','input':output}],'tool_use'
        executor=dslab.Executor(self.task,self.root,send=send)
        with redirect_stdout(io.StringIO()):value=json.loads(executor.run('Review',agent='reviewer',readonly=True))
        self.assertEqual(value,example);self.assertEqual(len(requests),4)
        tool=next(t for t in requests[0]['tools'] if t['name']=='submit_result')
        self.assertEqual(tool['input_schema']['type'],'object')
        self.assertTrue(all(t['name']!='write_file' for t in requests[0]['tools']))
        self.assertEqual({t['name'] for t in requests[1]['tools']},{'submit_patch','read_patch_field'})
        self.assertNotIn('"findings"',requests[1]['messages'][0]['content'])
        initial_system=requests[0]['system'][0]['text']
        self.assertIn('Trusted output format',initial_system)
        self.assertIn('Arrays stay arrays',initial_system)
        detail=json.loads(requests[1]['messages'][0]['content'])
        self.assertEqual(detail['issues'][0]['path'],'$.reviewed_condition_ids')
        self.assertEqual(detail['issues'][0]['received_type'],'string')

    def test_invalid_json_gets_exact_json_contract_feedback_without_writes(self):
        example=load_example('ReviewResult');answers=iter(['{"contract":',json.dumps(example)])
        requests=[]
        def send(request,*args,**kwargs):
            requests.append(copy.deepcopy(request));return [{'type':'text','text':next(answers)}],'end_turn'
        executor=dslab.Executor(self.task,self.root,send=send)
        with redirect_stdout(io.StringIO()):result=json.loads(executor.run('Review',agent='reviewer',readonly=True))
        self.assertEqual(result,example)
        feedback=json.loads(requests[1]['messages'][-1]['content'])
        self.assertEqual(feedback['issues'][0]['code'],'json_syntax')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_coder_cannot_choose_reviewer_output_or_specialist_submit(self):
        task={**self.task,'role':'coder'}
        executor=dslab.Executor(task,self.root)
        with self.assertRaises(ValueError):executor.pipeline_contract('main',False)
        executor=dslab.Executor(self.task,self.root)
        with self.assertRaises(ValueError):executor.tool('submit_result',load_example('ReviewResult'),agent='tester-1',readonly=True)

    def test_reviewer_uses_only_the_read_only_executor_and_cli_schema(self):
        with patch.object(daemon,'EXECUTOR_ROLE','planner'),patch.object(daemon,'PROVIDER','any'):
            self.assertTrue(daemon.matches_executor(self.task))
            command=daemon.build_command({**self.task,'provider':'codex','model':'','reasoning_effort':'default'},self.root)
            self.assertIn('--output-schema',command)
            self.assertIn('features.multi_agent=false',command)
            self.assertTrue(command[command.index('--output-schema')+1].endswith('ReviewResult.submission.schema.json'))
        with patch.object(daemon,'EXECUTOR_ROLE','coder'):
            self.assertFalse(daemon.matches_executor(self.task))
            with self.assertRaises(ValueError):daemon.build_command(self.task,self.root)

    def test_cli_rejects_incompatible_schema_before_a_model_can_start(self):
        with (patch.object(daemon,'EXECUTOR_ROLE','planner'),
              patch('common.pipeline_contracts.load_submission_schema',side_effect=ValueError('Invalid generation schema')) as preflight):
            with self.assertRaisesRegex(ValueError,'Invalid generation schema'):
                daemon.build_command({**self.task,'provider':'codex','model':'','reasoning_effort':'default'},self.root)
        preflight.assert_called_once_with('ReviewResult')

    def test_native_format_tool_matches_actual_required_generation_fields(self):
        from common.pipeline_contracts import submission_schema
        task={**self.task,'role':'planner','output_contract':'RequirementsCatalog'}
        executor=dslab.Executor(task,self.root)
        guide=json.loads(executor.tool('read_output_format',{},agent='planner',readonly=True))
        self.assertEqual(guide['schema'],submission_schema('RequirementsCatalog'))
        self.assertIn('question_resolutions',guide['schema']['required'])
        legacy=load_example('RequirementsCatalog');del legacy['question_resolutions']
        with self.assertRaises(ValueError):executor.tool('submit_result',legacy,agent='planner',readonly=True)

    def test_native_missing_optional_storage_field_is_corrected_as_new_submission(self):
        example=load_example('RequirementsCatalog');old=copy.deepcopy(example);del old['question_resolutions']
        task={**self.task,'role':'planner','output_contract':'RequirementsCatalog'}
        requests=[]
        def send(request,*a,**k):
            requests.append(copy.deepcopy(request))
            return [{'type':'tool_use','id':'call'+str(len(requests)),
                'name':'submit_result' if len(requests)==1 else 'submit_patch',
                'input':old if len(requests)==1 else field_patch(request,example)}],'tool_use'
        with redirect_stdout(io.StringIO()):
            value=json.loads(dslab.Executor(task,self.root,send=send).run('Fixture',agent='planner',readonly=True))
        self.assertEqual(value['question_resolutions'],[])
        self.assertEqual(len(requests),2)
        self.assertEqual(json.loads(requests[1]['messages'][0]['content'])['allowed_paths'],['$.question_resolutions'])

    def test_inspect_artifact_hashes_real_files_but_never_credentials(self):
        import hashlib
        (self.root/'src.txt').write_text('actual source',encoding='utf-8')
        executor=dslab.Executor(self.task,self.root)
        value=json.loads(executor.tool('inspect_artifact',{'path':'src.txt'},agent='reviewer',readonly=True))
        self.assertEqual(value['sha256'],hashlib.sha256(b'actual source').hexdigest())
        (self.root/'.env').write_text('not a real credential',encoding='utf-8')
        with self.assertRaises(ValueError):executor.tool('inspect_artifact',{'path':'.env'},readonly=True)

    def test_portable_generation_never_waives_the_full_path_validator(self):
        from common.pipeline_contracts import submission_schema,validate
        from jsonschema import Draft202012Validator
        example=load_example('CoderReport')
        schema=submission_schema('CoderReport')
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(example)
        example['changed_files'][0]['path']='../outside.java'
        # Generation grammar is provider-portable; safety belongs to the full
        # trusted validator and filesystem reader, regardless of model output.
        Draft202012Validator(schema).validate(example)
        with self.assertRaises(ValueError):validate('CoderReport',example)

    def test_malformed_tool_json_gets_correction_in_same_conversation_and_preserves_usage(self):
        malformed='{"reviewed_condition_ids": ["C1"],'
        events=[{'type':'message_start','message':{'usage':{'input_tokens':99}}},
            {'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':'PRIVATE_THINKING','signature':'PRIVATE_SIGNATURE'}},
            {'type':'content_block_start','index':1,'content_block':{'type':'tool_use','id':'bad','name':'submit_result','input':{}}},
            {'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':malformed}},
            {'type':'content_block_stop','index':1},
            {'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':123}},
            {'type':'message_stop'}]
        response=io.BytesIO(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode());response.status=200
        connection=MagicMock();connection.getresponse.return_value=response
        requests=[];example=load_example('ReviewResult')
        def send(request,*a,**k):
            requests.append(copy.deepcopy(request))
            if len(requests)==1:
                return dslab.streamed_message({},'synthetic-capability',agent='reviewer',connection_factory=lambda **_:connection)
            return [{'type':'tool_use','id':'valid','name':'submit_result','input':example}],'tool_use'
        with redirect_stdout(io.StringIO()) as output:
            result=json.loads(dslab.Executor(self.task,self.root,send=send).run('Fixture',agent='reviewer',readonly=True))
        self.assertEqual(result,example);self.assertEqual(len(requests),2)
        history=requests[1]['messages']
        self.assertEqual(history[-2]['content'][0]['signature'],'PRIVATE_SIGNATURE')
        self.assertFalse(any('_argument_error' in b for b in history[-2]['content']))
        feedback=json.loads(history[-1]['content'][0]['content'])
        issue=feedback['issues'][0]
        self.assertNotIn('raw_arguments',issue)
        records=[json.loads(line)['record'] for line in output.getvalue().splitlines()
                 if json.loads(line).get('type')=='correction.record']
        self.assertEqual(records[0]['submission']['invalid_tool_arguments'][0]['raw_arguments'],malformed)
        self.assertEqual(records[-1]['resolves'],[feedback['correction_id']])
        self.assertGreater(issue['column'],0)
        self.assertIn('"output_tokens": 123',output.getvalue())
        self.assertNotIn('PRIVATE_THINKING',output.getvalue());self.assertNotIn('PRIVATE_SIGNATURE',output.getvalue())

    def test_rejected_batch_executes_neither_bad_call_nor_valid_writer(self):
        task={**self.task,'role':'coder','output_contract':'CoderReport'};example=load_example('CoderReport');requests=[]
        def send(request,*a,**k):
            requests.append(copy.deepcopy(request))
            if len(requests)==1:return [
                {'type':'tool_use','id':'bad','name':'read_file','input':{},'_argument_error':{'code':'tool_arguments_json','raw_arguments':'{"path":'}},
                {'type':'tool_use','id':'write','name':'write_file','input':{'path':'must-not-exist.txt','content':'not authorized by rejected batch'}}],'tool_use'
            return [{'type':'tool_use','id':'final','name':'submit_result','input':example}],'tool_use'
        with redirect_stdout(io.StringIO()):
            dslab.Executor(task,self.root,send=send).run('Fixture',agent='main',readonly=False)
        self.assertFalse((self.root/'must-not-exist.txt').exists())
        results=requests[1]['messages'][-1]['content']
        self.assertEqual({r['tool_use_id'] for r in results},{'bad','write'})
        self.assertTrue(all(r['is_error'] for r in results))

    def test_non_object_tool_arguments_receive_feedback_not_python_attribute_error(self):
        answers=iter([{'type':'tool_use','id':'bad','name':'submit_result','input':['not an object']},
                      {'type':'tool_use','id':'good','name':'submit_result','input':load_example('ReviewResult')}])
        requests=[]
        def send(request,*a,**k):
            requests.append(copy.deepcopy(request));return [next(answers)],'tool_use'
        with redirect_stdout(io.StringIO()):
            dslab.Executor(self.task,self.root,send=send).run('Fixture',agent='reviewer',readonly=True)
        feedback=json.loads(requests[1]['messages'][-1]['content'][0]['content'])
        self.assertEqual(feedback['issues'][0]['code'],'tool_arguments_type')

    def test_invalid_sse_json_is_transport_failure_with_location_not_model_feedback(self):
        response=io.BytesIO(b'data: {broken event}\n\n');response.status=200
        connection=MagicMock();connection.getresponse.return_value=response
        with redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'invalid JSON event at line 1 column'):
            dslab.streamed_message({},'fixture',connection_factory=lambda **_:connection)

    def test_valid_json_wrong_sse_shape_is_protocol_failure(self):
        response=io.BytesIO(b'data: []\n\n');response.status=200
        connection=MagicMock();connection.getresponse.return_value=response
        with redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'invalid JSON event shape'):
            dslab.streamed_message({},'fixture',connection_factory=lambda **_:connection)

    def test_tool_block_without_close_never_executes_placeholder_arguments(self):
        events=[{'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':'bad','name':'read_file','input':{}}},
                {'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'{"path":"file"}'}},
                {'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'input_tokens':1,'output_tokens':5}},
                {'type':'message_stop'}]
        response=io.BytesIO(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode());response.status=200
        connection=MagicMock();connection.getresponse.return_value=response
        with redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'tool block not closed'):
            dslab.streamed_message({},'fixture',connection_factory=lambda **_:connection)


if __name__=='__main__':unittest.main()

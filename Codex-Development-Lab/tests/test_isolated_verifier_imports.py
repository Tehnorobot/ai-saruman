"""Exercise the real -I supervisor import graph, not pytest's augmented sys.path."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path


@unittest.skipUnless(os.name=='posix','Production verifier uses Linux descriptor-relative reads')
class IsolatedVerifierImportsTests(unittest.TestCase):
    def test_actual_supervisor_runs_scenario_collector_without_workspace_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);workspace=root/'workspace';workspace.mkdir()
            # Even a malicious cwd and PYTHONPATH cannot replace image-owned helpers.
            for name in ('common','worker'):
                package=workspace/name;package.mkdir()
                (package/'__init__.py').write_text("raise RuntimeError('UNTRUSTED_IMPORT_EXECUTED')")
            entry=Path(__file__).resolve().parents[1]/'worker/sandbox_entry.py'
            program=r'''
import json,runpy,sys
from pathlib import Path
namespace=runpy.run_path(sys.argv[1],run_name='trusted_supervisor_test')
from common import failure_details
import acceptance_runner
image=Path(sys.argv[1]).resolve().parent.parent
assert Path(failure_details.__file__).resolve().is_relative_to(image)
assert Path(acceptance_runner.__file__).resolve().is_relative_to(image/'worker')
assert str(Path.cwd()) not in sys.path
root=Path(sys.argv[2]);build=root/'build';build.mkdir();output=root/'output';output.mkdir()
scenario={'id':'combat','kind':'unit','task':'testCombat','requirement_ids':['REQ-1'],
          'expected_tests':[{'classname':'Combat','name':name} for name in ('select','hit','stop')]}
def command(argv,cwd,log,timeout,**kwargs):
    reports=build/'test-results';reports.mkdir(exist_ok=True)
    (reports/'results.xml').write_text('<testsuite failures="1"><testcase classname="Combat" name="select"/>'
      '<testcase classname="Combat" name="hit"><failure message="No hit"/></testcase></testsuite>')
    return {'status':'FAIL','exit_code':1}
result=acceptance_runner.run_scenarios({'schema_version':1,'spec_sha256':'a'*64,'scenarios':[scenario]},
  {'gradle':['gradle']},Path.cwd(),output,60,command,None,build=build)['scenarios'][0]
assert result['status']=='FAIL'
assert result['failure_details']['counts']['passed']==1,result
assert result['failure_details']['counts']['failed']==1,result
assert result['failure_details']['counts']['not_executed']==1,result
assert (output/'acceptance_results.json').is_file()
print(json.dumps({'isolated':sys.flags.isolated,'status':result['status'],'counts':result['failure_details']['counts']}))
'''
            response=subprocess.run([sys.executable,'-I','-c',program,str(entry),str(root)],
                cwd=workspace,env={**os.environ,'PYTHONPATH':str(workspace)},capture_output=True,text=True,timeout=20)
            self.assertEqual(response.returncode,0,response.stdout+'\n'+response.stderr)
            data=json.loads(response.stdout)
            self.assertEqual(data['isolated'],1)
            self.assertEqual(data['counts']['passed'],1)
            self.assertNotIn('UNTRUSTED_IMPORT_EXECUTED',response.stderr)

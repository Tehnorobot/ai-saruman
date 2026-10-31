import io,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import db
from worker import engine
from backup import daemon as backup
from app import main as dashboard
from fastapi.testclient import TestClient

class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)
        self.projects=p/'projects';self.projects.mkdir()
        self.artifacts=p/'artifacts';self.artifacts.mkdir()
        self.backups=p/'backups';self.backups.mkdir()
        self.requests=p/'requests';self.requests.mkdir()
        db.DB=p/'db'/'lab.sqlite';db.init()
        dashboard.PROJECTS=self.projects;dashboard.ARTIFACTS=self.artifacts
        engine.WORKSPACE_ROOT=self.projects;engine.ARTIFACTS=self.artifacts
        backup.PROJECTS=self.projects;backup.ARTIFACTS=self.artifacts;backup.OUTPUT=self.backups;backup.REQUESTS=self.requests
        self.env=patch.dict(os.environ,{'LAB_PROJECTS':str(self.projects),'LAB_ARTIFACTS':str(self.artifacts),'LAB_BACKUPS':str(self.backups),'BACKUP_REQUESTS':str(self.requests)},clear=False)
        self.env.start();self.addCleanup(self.env.stop)
        self.client=TestClient(dashboard.app)

    def test_upload_one_file_creates_queue(self):
        res=self.client.post('/api/projects',files={'spec':('ТЗ.md',b'# Test project\nBuild a real program from one file.','text/markdown')},data={'max_rounds':'3'})
        self.assertEqual(res.status_code,200,res.text)
        j=res.json();self.assertEqual(db.job_row(j['job_id'])['status'],'QUEUED')
        self.assertEqual((self.projects/j['project_id']/'ТЗ.md').read_text(),'# Test project\nBuild a real program from one file.')
        self.assertEqual(len(self.client.get('/api/jobs').json()),1)

    def test_invalid_spec_and_limit(self):
        a=self.client.post('/api/projects',files={'spec':('x.txt',b'anything longer than twenty bytes','text/plain')})
        self.assertEqual(a.status_code,400)
        for value in ('-1', str(2**53)):
            b=self.client.post('/api/projects',files={'spec':('x.md',b'A'*50,'text/markdown')},data={'max_rounds':value})
            self.assertEqual(b.status_code,400)

    def test_claim_single_owner(self):
        pid,jid=db.create_project('P','ТЗ.md',b'# Enough details for a test project',2)
        first=db.claim('workerA');second=db.claim('workerB')
        self.assertEqual(first['id'],jid);self.assertIsNone(second)
        self.assertEqual(db.job_row(jid)['worker'],'workerA')

    def test_pause_resume_stop_routes(self):
        pid,jid=db.create_project('P','spec.md',b'# Enough details for a test project',2)
        self.assertEqual(self.client.post('/api/jobs/'+jid+'/pause').json()['status'],'USER_PAUSED')
        self.assertIsNone(db.claim('w'))
        self.assertEqual(self.client.post('/api/jobs/'+jid+'/resume').json()['status'],'QUEUED')
        self.assertEqual(db.claim('w')['id'],jid)
        self.client.post('/api/jobs/'+jid+'/stop')
        self.assertEqual(db.job_row(jid)['cancel_requested'],1)

    def test_quota_and_auth_distinct(self):
        self.assertTrue(engine.is_quota('ERROR: You have reached your weekly limit, reset tomorrow'))
        self.assertTrue(engine.is_quota('{"type":"error","code":"rate_limit_exceeded"}'))
        self.assertFalse(engine.is_quota('Authentication required: login'))
        self.assertFalse(engine.is_quota('Java compile error: cannot resolve symbol'))

    def test_safe_download_does_not_escape_directory(self):
        pid,jid=db.create_project('P','spec.md',b'# Enough details for a test project',2)
        self.assertEqual(self.client.get('/api/jobs/'+jid+'/files/../../../etc/passwd').status_code,404)
        d=self.artifacts/pid/jid;d.mkdir(parents=True)
        (d/'test.log').write_text('real-log')
        r=self.client.get('/api/jobs/'+jid+'/files/test.log')
        self.assertEqual(r.status_code,200);self.assertEqual(r.content,b'real-log')

    def test_backup_contains_only_allowlisted_files_and_manifest(self):
        pid,jid=db.create_project('P','spec.md',b'# Enough details for a test project',2)
        workspace=self.projects/pid/'workspace';(workspace/'module.py').write_text('print(1)')
        (workspace/'secret.mp4').write_bytes(b'large-video-not-copied')
        (self.artifacts/pid/jid).mkdir(parents=True)
        dest=backup.create_backup(jid,'quota_exhausted')
        import tarfile
        with tarfile.open(dest) as t:
            files=t.getnames()
            self.assertIn('backup_manifest.json',files)
            self.assertIn('project/workspace/module.py',files)
            self.assertIn('state/lab.sqlite3',files)
            self.assertNotIn('project/workspace/secret.mp4',files)
            manifest=json.load(t.extractfile('backup_manifest.json'))
            self.assertEqual(manifest['reason'],'quota_exhausted')
        self.assertTrue(dest.with_suffix(dest.suffix+'.sha256').exists())
        self.assertEqual(self.client.get('/api/jobs/'+jid+'/backups/'+dest.name).status_code,200)

    def test_events_cannot_be_faked_by_model_output(self):
        pid,jid=db.create_project('P','spec.md',b'# Enough details for a test project',2)
        root=self.artifacts/pid/jid;root.mkdir(parents=True)
        v=self.client.get('/api/jobs/'+jid+'/verification').json()
        self.assertEqual(v['status'],'NOT_TESTED')
        self.assertEqual(self.client.get('/api/jobs/'+jid+'/s3').json(),[])

if __name__=='__main__':unittest.main()

# Extra end-to-end state test with a fake Codex transport. No claims about Minecraft.
class WorkerIntegrationTests(unittest.TestCase):
    def test_quota_preserves_project_and_triggers_backup_request(self):
        from common import db
        from worker import engine
        from backup import daemon as backup
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);db.DB=root/'data'/'lab.sqlite'
            projects=root/'projects';projects.mkdir()
            artifacts=root/'artifacts';artifacts.mkdir()
            requests=root/'requests';requests.mkdir()
            backups=root/'backups';backups.mkdir()
            with patch.dict(os.environ,{'LAB_PROJECTS':str(projects),'LAB_ARTIFACTS':str(artifacts),'BACKUP_REQUESTS':str(requests)}):
                pid,jid=db.create_project('Quota test','ТЗ.md',b'# A development specification for an app',1)
                engine.WORKSPACE_ROOT=projects;engine.ARTIFACTS=artifacts
                backup.PROJECTS=projects;backup.ARTIFACTS=artifacts;backup.REQUESTS=requests;backup.OUTPUT=backups
                def fake_codex(label,argv,cwd,logfile,timeout,job,**kw):
                    logfile.parent.mkdir(exist_ok=True,parents=True)
                    logfile.write_text('Rate limit reached. You have reached your weekly limit')
                    return {'label':'codex','status':'FAIL','tail':'You have reached your weekly limit'}
                with patch.object(engine,'plan_round',side_effect=lambda jid,pid,n,ws,previous,stage,context:({'status':'PASS'},context)),patch.object(engine,'copy_skill_environment',lambda x: x/'workspace'),patch.object(engine,'request_agent',lambda *a,**kw: fake_codex('codex',[],None,root/'fake.log',0,jid)):
                    engine.replay_legacy(jid)
                row=db.job_row(jid)
                self.assertEqual(row['status'],'QUOTA_PAUSED')
                self.assertGreater(row['resume_at'],0)
                self.assertEqual(len(list(requests.glob('*.json'))),1)
                backup.run_once()
                self.assertEqual(len(list((backups/jid).glob('*.tar.gz'))),1)
                self.assertIsNone(db.claim('another_worker'))

class AgentExecutorTests(unittest.TestCase):
    def test_agent_executes_one_task_with_fake_codex_and_records_exit_code(self):
        from agent import daemon as executor
        import tempfile,json,os
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);mail=d/'mail';project=d/'projects';pid='abcdef01234567';jid='1234567890abcd'
            workspace=project/pid/'workspace';workspace.mkdir(parents=True)
            for part in ('requests','results','logs','cancel'):(mail/part).mkdir(parents=True)
            dummy=d/'fake_codex.py'
            dummy.write_text('print(\'{"type":"turn.completed"}\')\n')
            taskid=jid+'-round01';req=mail/'requests'/f'{taskid}.json'
            req.write_text(json.dumps({'id':taskid,'job_id':jid,'project_id':pid,'prompt':'Read ТЗ.md'}))
            real_popen = subprocess.Popen
            def fake_popen(argv, **kwargs):
                return real_popen([sys.executable, str(dummy), *argv[1:]], **kwargs)
            with patch.object(executor,'ROOT',mail),patch.object(executor,'PROJECTS',project),patch.object(executor.subprocess,'Popen',side_effect=fake_popen):
                executor.run_task(req)
            result=json.loads((mail/'results'/f'{taskid}.json').read_text())
            self.assertEqual(result['status'],'PASS')
            self.assertTrue((mail/'logs'/f'{taskid}.jsonl').exists())
            self.assertFalse(req.exists())

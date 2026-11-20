import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from mage_home import MageError, expert, same_token, select_attempts, validate_patch
from mage_home.blocks import xyz
from mage_home.providers import load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT/'profiles/replay.json'


def fixture(name):
    return json.loads((ROOT/'replay'/name).read_text(encoding='utf-8'))


def cli(action, raw, profile=PROFILE):
    p = subprocess.run([sys.executable,'-m','mage_home',action,'--profile',str(profile)],input=raw,cwd=ROOT,capture_output=True,check=True,timeout=15)
    assert p.stderr == b'',p.stderr
    assert p.stdout.count(b'\n') == 1,p.stdout
    return json.loads(p.stdout.decode('utf-8'))


def encode(value):
    return json.dumps(value,ensure_ascii=False).encode('utf-8')


class ExpertTests(unittest.TestCase):
    def setUp(self):
        self.provider,_ = load_profile(PROFILE)
        self.access = fixture('request-expert-access.json')
        self.mismatch = fixture('request-expert-mismatch.json')

    def test_two_distinct_repair_scenarios_and_exact_freshness(self):
        mismatch = expert(self.mismatch,self.provider)
        self.assertEqual([s['kind'] for s in mismatch['repair']['steps']],['break_block','place_block'])
        self.assertEqual(mismatch['repair']['expects']['block'],'minecraft:stone_bricks')
        self.assertEqual(mismatch['repair']['steps'][1]['dependsOn'],['expert-clear'])
        access = expert(self.access,self.provider)
        self.assertEqual([s['kind'] for s in access['repair']['steps']],['move','place_block'])
        self.assertEqual(access['repair']['route'],self.access['attemptLedger']['records'][0]['cells'])
        self.assertTrue(same_token(access,self.access))
        for field in ('generation','schemaRevision','permitGeneration'):
            self.assertFalse(same_token(access,{**self.access,field:self.access[field]+1}),field)
        for field in ('requestId','runId','worldSnapshotRevision'):
            self.assertFalse(same_token(access,{**self.access,field:'changed'}),field)
        for state in ('STOPPED','PAUSED','READY','BLOCKED'):
            self.assertFalse(same_token(access,{**self.access,'state':state}),state)
        access['generation'] += 1
        with self.assertRaisesRegex(MageError,'different request'):
            validate_patch(access,self.access)

    def test_continuous_recovery_accepts_unbounded_incident_counter(self):
        self.mismatch['options']['continuousRecovery'] = True
        incident = self.mismatch['incident']
        incident.pop('attemptsBudgetTotal')
        incident['attemptsBudgetUsed'] = 2500
        proposal = expert(self.mismatch,self.provider)
        self.assertEqual([step['kind'] for step in proposal['repair']['steps']], ['break_block','place_block'])

    def test_routes_use_actual_supports_and_real_changed_clearance(self):
        self.access['attemptLedger']['records'].append({**self.access['attemptLedger']['records'][0],'id':'foreign-run','runId':'unrelated'})
        records,evidence = select_attempts(self.access)
        self.assertEqual([r['id'] for r in records],['route-north','route-west','route-south'])
        self.assertEqual([e['status'] for e in evidence],['valid','avoid_unchanged_or_unobserved','not_blocked'])
        world = {(c['x'],c['y'],c['z']):c for c in self.access['worldSnapshot']['cells']}
        world[(10,-60,21)]['block'] = 'minecraft:air'
        _,evidence = select_attempts(self.access)
        self.assertEqual(evidence[0]['status'],'invalid_or_unknown')
        self.assertEqual(expert(self.access,self.provider)['outcome'],'blocked')
        world[(9,-58,20)]['block'] = 'minecraft:air'
        _,evidence = select_attempts(self.access)
        self.assertEqual(evidence[1]['status'],'clearance_now_observed')
        self.assertEqual(expert(self.access,self.provider)['repair']['route'],[{'x':9,'y':-59,'z':20}])

    def test_scaffold_access_requires_an_advertised_stance_and_goal_dependency(self):
        request = self.access
        base = {'x':9,'y':-59,'z':20}
        stance = {'x':9,'y':-58,'z':20}
        request['ownership']['availableActions'].append('scaffold_access')
        request['resources'] = {'scaffoldAccess': [{'base':base,'stance':stance,'blocksNeeded':1}]}
        proposal = expert(request,self.provider)
        repair = proposal['repair']
        repair['route'] = []
        repair['steps'][0].update(kind='scaffold_access', cells=[stance])
        self.assertEqual(validate_patch(proposal, request)['repair']['steps'][0]['cells'], [stance])
        repair['steps'][1]['dependsOn'] = []
        with self.assertRaisesRegex(MageError, 'must depend on scaffold access'):
            validate_patch(proposal, request)
        repair['steps'][1]['dependsOn'] = [repair['steps'][0]['id']]
        repair['steps'][0]['cells'] = [{**stance,'x':8}]
        with self.assertRaisesRegex(MageError, 'observed scaffold stance'):
            validate_patch(proposal, request)

    def test_rejects_unknown_mutation_unauthorized_command_and_goal_rewrite(self):
        raw = expert(self.mismatch,self.provider)
        replayed = copy.deepcopy(self.mismatch); replayed['worldSnapshot']['cells']=[]
        with self.assertRaisesRegex(MageError,'unknown cell'):
            validate_patch(raw,replayed)
        command = copy.deepcopy(raw)
        command['repair']['steps'][1]['kind'] = 'send_command'
        with self.assertRaisesRegex(MageError,'unauthorized action'):
            validate_patch(command,self.mismatch)
        modified = copy.deepcopy(raw); modified['repair']['steps'][1]['cells'][0]['block']='minecraft:gold_block'
        with self.assertRaisesRegex(MageError,'mandatory goal'):
            validate_patch(modified,self.mismatch)
        modified = copy.deepcopy(raw); modified['repair']['steps'][1]['cells'][0]['command']='/tp @a 0 0 0'
        with self.assertRaisesRegex(MageError,'invalid step cell fields'):
            validate_patch(modified,self.mismatch)
        modified = copy.deepcopy(raw); modified['repair']['steps'][0]['dependsOn']=['expert-set']
        with self.assertRaisesRegex(MageError,'future'):
            validate_patch(modified,self.mismatch)

    def test_off_goal_opening_and_repair_have_ordered_exact_restoration(self):
        req = self.access
        req['ownership']['availableActions'].append('break_block')
        obstructed = {'x':9,'y':-58,'z':20,'block':'minecraft:stone_bricks','state':{}}
        route = [{'x':9,'y':-59,'z':20}]
        target = {**req['incident']['goal']['cell'],'block':'minecraft:stone_bricks','state':{}}
        opening = {'id':'clear-route','kind':'break_block','cells':[obstructed],'cleanupIds':['restore-wall']}
        move = {'id':'move-route','kind':'move','cells':route,'dependsOn':['clear-route']}
        place = {'id':'place-goal','kind':'place_block','cells':[target],'dependsOn':['move-route']}
        restore = {'id':'restore','kind':'place_block','cells':[obstructed],'dependsOn':['place-goal'],'cleanupIds':['restore-wall']}
        response = {'outcome':'repair','reason':'Open an observed passage temporarily, then restore it.',
                    'repair':{'steps':[opening,move,place,restore],'cleanupIds':['restore-wall'],'route':route,
                              'expects':{'at':req['incident']['goal']['cell'],'block':'minecraft:stone_bricks'}}}
        _, evidence = select_attempts(req)
        self.assertEqual(validate_patch(response,req,evidence=evidence)['repair']['cleanupIds'],['restore-wall'])
        invalid = copy.deepcopy(response); invalid['repair']['steps'].pop()
        with self.assertRaisesRegex(MageError,'unfulfilled route or cleanup'):
            validate_patch(invalid,req,evidence=evidence)
        invalid = copy.deepcopy(response); invalid['repair']['steps'][3]['dependsOn']=[]
        with self.assertRaisesRegex(MageError,'depend on the previous change'):
            validate_patch(invalid,req,evidence=evidence)

    def test_observation_blocked_or_exhausted_are_typed(self):
        p = self.access['incident']['goal']['cell']
        self.access['worldSnapshot']['cells'] = [c for c in self.access['worldSnapshot']['cells'] if xyz(c)!=xyz(p)]
        self.access['worldSnapshot']['known']=[p]
        self.assertEqual(expert(self.access,self.provider)['outcome'],'request_observation')
        self.access['ownership']['availableActions'].remove('observe')
        self.assertEqual(expert(self.access,self.provider)['outcome'],'blocked')
        self.access['incident']['attemptsBudgetUsed']=3
        with self.assertRaises(MageError) as err:
            expert(self.access,self.provider)
        self.assertEqual(err.exception.code,'incident_budget_exhausted')

    def test_configured_replay_can_reproduce_deadline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'experts.json').write_text(json.dumps([{'kind':'access_lost','delayMillis':200,'response':{}}]),encoding='utf-8')
            profile = root/'delay.json'
            profile.write_text(json.dumps({'schema':'mage_home.profile/1','provider':'replay','expertReplay':'experts.json','deadlineMillis':40}),encoding='utf-8')
            result = cli('expert',encode(self.access),profile)
            self.assertEqual(result['error']['code'],'provider_timeout')


class CliTests(unittest.TestCase):
    def test_cli_uses_utf8_and_one_object_per_invocation(self):
        design = fixture('request-courtyard.json')
        result = cli('design',encode(design))
        self.assertEqual(result['blueprint']['description'],design['description'])
        self.assertEqual(result['blueprint']['projectId'],'example-courtyard')
        for request_file in ('request-expert-mismatch.json','request-expert-access.json'):
            result = cli('expert',encode(fixture(request_file)))
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['proposal']['outcome'],'repair')
        for invalid in (b'{} {}',b'{"a":1,"a":2}',b'{"value": NaN}',b'\xff'):
            self.assertEqual(cli('design',invalid)['error']['code'],'invalid_json')

    def test_standalone_provider_transport_against_loopback_stub_not_live_model(self):
        designs = fixture('designs.json')
        seen = []
        valid = designs[0]['response']
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((self.path,self.headers.get('Authorization'),req))
                answer = {'choices':[{'message':{'content':json.dumps(valid)}}]}
                raw = encode(answer)
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as directory:
                profile = Path(directory)/'profile.json'
                cfg = {'schema':'mage_home.profile/1','provider':'openai_compatible','allowLoopbackHttp':True,
                       'endpoint':f'http://127.0.0.1:{server.server_port}/v1/chat/completions','model':'test-stub','deadlineMillis':3000,'projectDirectory':'projects',
                       'designCacheDirectory':'design-cache'}
                profile.write_text(json.dumps(cfg),encoding='utf-8')
                req = fixture('request-courtyard.json'); req['context']['player']={'name':'do-not-send'}; req['context']['world']['seed']=12345
                result = cli('design',encode(req),profile)
                self.assertTrue(result['ok'],result)
                path,auth,http_request = seen[0]
                self.assertEqual(path,'/v1/chat/completions'); self.assertIsNone(auth)
                self.assertEqual(http_request['model'],'test-stub')
                sent = json.loads(http_request['messages'][1]['content'])
                self.assertNotIn('player',sent); self.assertNotIn('world',sent)
                snapshots = list((Path(directory)/'projects').glob('*.json'))
                self.assertEqual(len(snapshots),1)
                self.assertEqual(json.loads(snapshots[0].read_text(encoding='utf-8')),result['blueprint'])
                repeated = cli('design',encode({**req,'projectId':'cached-project'}),profile)
                self.assertEqual(repeated['blueprint'],{**result['blueprint'],'projectId':'cached-project'})
                self.assertEqual(len(seen),1)
                req['context']['anchor']['x'] += 1
                self.assertTrue(cli('design',encode(req),profile)['ok'])
                self.assertEqual(len(seen),2)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

"""Complete memory is browsed by page and record, without changing job state."""
import json
import unittest

from common import acceptance, db
from tests import test_memory_view as fixture


class MemoryArchiveViewTests(unittest.TestCase):
    setUp=fixture.MemoryWindowTests.setUp

    def save(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def test_second_page_and_old_record_are_available_without_resuming_job(self):
        root=self.jobroot/'memory-archive'
        signature='a'*64
        self.save(root/'index.json',{'counts':{'errors':150,'notes':12},
            'error_pages':[{'path':'pages/errors-000001.json'},{'path':'pages/errors-000002.json'}]})
        self.save(root/'pages/errors-000002.json',{'items':[{'path':'errors/'+signature+'.json','first_round':1}]})
        self.save(root/'errors'/ (signature+'.json'),{'detail':'old unresolved problem '+'x'*20000,
            'status':'OPEN','first_round':1,'api_key':'private-credential'})
        before=db.job_row(self.jid)
        data=self.client.get(self.url+'/archive?kind=errors&page=2').json()
        self.assertEqual(data['page']['data']['items'][0]['first_round'],1)
        self.assertEqual(data['index']['data']['counts']['errors'],150)
        record=self.client.get(self.url+'/archive/record',params={'path':'.lab-memory/errors/'+signature+'.json'}).json()
        self.assertEqual(len(record['data']['detail']),20023)
        self.assertNotIn('private-credential',json.dumps(record))
        self.assertEqual(db.job_row(self.jid),before)

    def test_unknown_page_is_explicit_and_paths_are_whitelisted(self):
        data=self.client.get(self.url+'/archive?kind=notes&page=99').json()
        self.assertEqual(data['page']['state'],'missing')
        for path in ('../round-memory.json','index.json','agent-history/request.json',
                     '.lab-memory/../round-memory.json','errors/'+('a'*64)+'.json/../secret'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(self.url+'/archive/record',params={'path':path}).status_code,400)
        for query in ('kind=secrets','kind=errors&page=0','kind=notes&page=-1'):
            self.assertEqual(self.client.get(self.url+'/archive?'+query).status_code,400)

    def test_memory_exports_do_not_change_production_source_fingerprint(self):
        before=acceptance.source_fingerprint(self.ws)
        self.save(self.ws/'.lab-memory/index.json',{'counts':{'errors':300}})
        self.save(self.ws/'.lab-memory/errors'/('a'*64+'.json'),{'detail':'new archive export'})
        self.assertEqual(acceptance.source_fingerprint(self.ws),before)
        (self.ws/'real.java').write_text('class Real {}')
        self.assertNotEqual(acceptance.source_fingerprint(self.ws),before)

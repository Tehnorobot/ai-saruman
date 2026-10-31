"""Regression: service logs must never consume the gallery's first page."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from common import db,media_catalog
from app import main


class MediaCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name);self.artifacts=self.base/'artifacts'
        for context in (patch.object(db,'DB',self.base/'db.sqlite'),
                        patch.object(main,'ARTIFACTS',self.artifacts),
                        patch.dict(os.environ,{'LAB_PROJECTS':str(self.base/'projects')})):
            context.start();self.addCleanup(context.stop)
        self.pid,self.jid=db.create_project('Gallery','spec.md',b'# Requirements\nA sufficiently long requirement.')
        self.root=self.artifacts/self.pid/self.jid;self.root.mkdir(parents=True)
        self.client=TestClient(main.app);self.url='/api/jobs/'+self.jid+'/media'

    def media(self,number,name):
        folder=self.root/f'round-{number:02d}';folder.mkdir(exist_ok=True)
        path=folder/name;path.write_bytes(b'content');return path

    def test_more_than_1000_logs_cannot_hide_rounds_28_to_31(self):
        folder=self.root/'round-01';folder.mkdir()
        for i in range(1010):(folder/f'log-{i:04d}.json').write_text('{}')
        for n in range(28,32):
            for name in ('capture.mp4','capture.png','mod.jar'):self.media(n,name)
        old=self.client.get('/api/jobs/'+self.jid+'/files').json()
        self.assertEqual(len(old),1000)
        self.assertFalse(any(item['path'].startswith('round-28/') for item in old))
        before=db.job_row(self.jid)
        data=self.client.get(self.url).json()
        self.assertEqual(data['total'],12)
        self.assertEqual(data['files'][0]['round'],31)
        self.assertEqual({f['round'] for f in data['files']},{28,29,30,31})
        self.assertEqual(db.job_row(self.jid),before)

    def test_numeric_order_pagination_and_explicit_round(self):
        for n in (9,31,100):
            for i in range(5):self.media(n,f'mod{i}.jar')
        paths=[];offset=0
        while True:
            data=self.client.get(self.url,params={'limit':4,'offset':offset}).json()
            paths.extend(f['path'] for f in data['files'])
            if not data['has_more']:break
            offset=data['next_offset']
        self.assertEqual(len(paths),15);self.assertEqual(len(set(paths)),15)
        self.assertTrue(paths[0].startswith('round-100/'))
        filtered=self.client.get(self.url,params={'round_number':31}).json()
        self.assertEqual(filtered['total'],5)
        self.assertTrue(all(f['round']==31 for f in filtered['files']))

    def test_quality_metadata_and_url_encoding_preserved(self):
        path=self.media(31,'capture#.mp4')
        (path.parent/'video_capture.json').write_text(json.dumps({'status':'FAIL','reason':'blank','authorization':'secret'}))
        item=self.client.get(self.url).json()['files'][0]
        self.assertEqual(item['media_status'],'FAIL');self.assertEqual(item['media_reason'],'blank')
        self.assertIn('%23',item['url']);self.assertNotIn('secret',json.dumps(item))

    def test_root_logs_do_not_exhaust_the_media_budget(self):
        file=self.media(31,'mod.jar')
        walk=[(str(self.root),['round-31'],['unused.log']*100001),
              (str(file.parent),[],['mod.jar'])]
        with patch.object(media_catalog.os,'walk',return_value=iter(walk)):
            data=media_catalog.page(self.root,self.jid)
        self.assertEqual(data['total'],1);self.assertFalse(data['truncated'])

    def test_bounds_unknown_job_and_empty_selected_round(self):
        for params in ({'limit':0},{'limit':251},{'offset':-1},{'round_number':0}):
            self.assertEqual(self.client.get(self.url,params=params).status_code,400)
        self.assertEqual(self.client.get('/api/jobs/missing/media').status_code,404)
        data=self.client.get(self.url,params={'round_number':31}).json()
        self.assertEqual(data['files'],[]);self.assertFalse(data['has_more'])

    def test_symlink_files_and_directories_are_not_gallery_items(self):
        outside=self.base/'outside';outside.mkdir();(outside/'private.jar').write_bytes(b'private')
        try:
            (self.root/'round-31').symlink_to(outside,target_is_directory=True)
            (self.root/'private.jar').symlink_to(outside/'private.jar')
        except OSError:self.skipTest('Symlink creation privilege unavailable')
        data=self.client.get(self.url).json()
        self.assertEqual(data['files'],[]);self.assertNotIn(31,data['rounds'])


if __name__=='__main__':unittest.main()

"""Offline correction provenance and targeted reads; no model calls."""
import json
import unittest
from common import correction_journal as journal


class CorrectionJournalTests(unittest.TestCase):
    def test_nonfinite_invalid_answer_is_preserved_as_raw_json(self):
        saved=journal.record('ReviewResult',{'bad':float('nan')},[{'path':'$.bad','code':'json_value'}])
        self.assertIsInstance(saved['submission'],str)
        self.assertIn('NaN',saved['submission'])
        json.dumps(saved,allow_nan=False)

    def test_grouped_feedback_keeps_exact_paths_and_full_original(self):
        issues=[{'path':f'$.items[{n}].ids','code':'type','expected':'array','received_type':'string',
                 'example_json':'[]','instruction':'Array required'} for n in range(20)]
        store=journal.Journal()
        rejected=store.reject('ReviewResult',{'items':[{'ids':'bad'}]}, {'issues':issues})
        short=journal.compact_feedback({'issues':issues,'example':{'huge':'example'}},rejected['correction_id'])
        self.assertEqual(len(short['issues']),1)
        self.assertEqual(short['issues'][0]['count'],20)
        self.assertNotIn('example',short)
        self.assertEqual(store.read(rejected['correction_id'],'submission','$.items[0].ids')['value'],'bad')
        self.assertEqual(store.get(rejected['correction_id'])['issues'],issues)
        accepted=store.accept('ReviewResult',{'items':[{'ids':[]}]})
        self.assertEqual(store.read(rejected['correction_id'],'resolution')['value'],[accepted])
        self.assertEqual(accepted['outcome'],'accepted_format')

    def test_escaped_pages_stay_valid_and_reassemble_without_loss(self):
        value={'large':'\\"'*20000}
        expected=json.dumps(value,ensure_ascii=False,indent=2)
        offset=0;parts=[]
        while True:
            result=journal.page(value,offset,16000)
            encoded=json.dumps(result,ensure_ascii=False)
            self.assertLess(len(encoded),32000)
            result=json.loads(encoded)
            parts.append(result['json_fragment'])
            if result['next_offset'] is None:break
            offset=result['next_offset']
        self.assertEqual(''.join(parts),expected)

    def test_selector_and_identifier_are_data_only(self):
        with self.assertRaises(ValueError):journal.select({},'$.x.__class__()')
        with self.assertRaises(ValueError):journal.Journal().get('../secret')

    def test_historical_resolution_is_read_on_demand(self):
        bad=journal.record('ReviewResult',{'x':'bad'},[])
        good=journal.record('ReviewResult',{'x':[]},[],outcome='accepted_format',resolves=[bad['correction_id']])
        values={v['correction_id']:v for v in (bad,good)};reads=[]
        def read(ref):
            reads.append(ref['path']);return {'correction':values[ref['path']]}
        store=journal.Journal(read=read)
        store.sources={k:{'payload_ref':{'path':k},'resolves':v['resolves']} for k,v in values.items()}
        self.assertEqual(reads,[])
        self.assertEqual(store.read(bad['correction_id'],'resolution')['value'],[good])


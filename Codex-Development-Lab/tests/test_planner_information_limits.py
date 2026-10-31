"""Large valid inventories and long tool histories do not hide planner work."""
import json
import tempfile
import unittest
from pathlib import Path

from common import planning


class PlannerInformationLimitsTests(unittest.TestCase):
    def test_target_inventory_includes_requirements_after_24_and_scenarios_after_64(self):
        ids=['REQ-%05d' % n for n in range(80)]
        catalog={'requirements':[{'id':rid} for rid in ids]}
        plan={'scenarios':[{'id':'scenario-%d' % n,'requirement_ids':[ids[n%80]]} for n in range(100)]}
        options=planning.target_options(catalog,plan,{'current_task':{'requirement_ids':ids}})
        self.assertEqual(options['requirement_targets'],ids)
        self.assertEqual(len(options['scenario_targets']),100)
        self.assertIn('scenario-99',options['scenario_targets'])

    def test_target_inventory_still_respects_scope(self):
        options=planning.target_options({'requirements':[{'id':'a'},{'id':'b'}]},
            {'scenarios':[{'id':'one','requirement_ids':['a']},{'id':'two','requirement_ids':['b']}]},
            {'current_task':{'requirement_ids':['a']}})
        self.assertEqual(options['requirement_targets'],['a'])
        self.assertEqual(set(options['scenario_targets']),{'one'})

    def test_large_final_json_and_more_than_8mb_tool_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'planner.jsonl'
            value={'summary':'x'*40000,'action':{'target':'known'}}
            with path.open('w',encoding='utf-8') as stream:
                event=json.dumps({'item':{'type':'tool_result','text':'x'*32000}})+'\n'
                for _ in range(300):stream.write(event)
                stream.write(json.dumps({'item':{'type':'agent_message','agent_id':'planner','text':json.dumps(value)}})+'\n')
                stream.write(json.dumps({'type':'turn.completed'})+'\n')
            self.assertGreater(path.stat().st_size,8*1024*1024)
            self.assertEqual(planning.final_message(path),value)

    def test_tool_output_is_not_accepted_as_final_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'planner.jsonl'
            path.write_text(json.dumps({'item':{'type':'tool_result','text':'{}'}}))
            with self.assertRaisesRegex(ValueError,'final JSON'):
                planning.final_message(path)

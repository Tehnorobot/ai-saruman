"""Exercise Gradio's event injection, including missing browser event data."""

import unittest

import gradio as gr

from saruman.gradio_events import on_tool_select


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with gr.Blocks() as self.app:
            self.table = gr.Dataframe(
                headers=["tool", "enabled", "calls"],
                value=[["agent_action", "ON", 0]],
                interactive=False,
            )
            output = gr.Textbox(value="previous_tool")
            self.table.select(on_tool_select, outputs=output)

    async def select(self, data):
        result = await self.app.process_api(
            0, [], event_data=gr.EventData(self.table, data)
        )
        return result["data"][0]

    async def test_null_selection_payload_does_not_crash_gradio(self):
        self.assertEqual(await self.select(None), gr.skip())

    async def test_missing_event_object_keeps_previous_tool(self):
        with self.assertWarnsRegex(UserWarning, "Unexpected argument"):
            result = await self.app.process_api(0, [])
        self.assertEqual(result["data"][0], gr.skip())

    async def test_invalid_selection_payloads_are_ignored(self):
        for data in (
            {},
            [],
            "invalid",
            {"index": None},
            {"index": [0], "value": "agent_action"},
            {"index": [None, None], "value": "agent_action"},
            {"index": [-1, 0], "value": "agent_action"},
            {"index": [False, 0], "value": "agent_action"},
            {"index": [0, 0], "value": None},
        ):
            with self.subTest(data=data):
                self.assertEqual(await self.select(data), gr.skip())

    async def test_other_columns_and_deselection_keep_previous_tool(self):
        for data in (
            {"index": [0, 1], "value": "ON"},
            {"index": [0, 2], "value": 0},
            {"index": [0, 0], "value": "agent_action", "selected": False},
        ):
            with self.subTest(data=data):
                self.assertEqual(await self.select(data), gr.skip())

    async def test_real_tool_cell_selection_still_fills_the_input(self):
        self.assertEqual(
            await self.select(
                {"index": [0, 0], "value": "agent_action", "selected": True}
            ),
            "agent_action",
        )


if __name__ == "__main__":
    unittest.main()

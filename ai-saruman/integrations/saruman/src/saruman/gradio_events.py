"""UI event handlers that tolerate empty selection payloads."""

import gradio as gr


def on_tool_select(evt: gr.EventData):
    # SelectData indexes the payload before the callback can validate it.
    data = getattr(evt, "_data", None)
    if not isinstance(data, dict) or not data.get("selected", True):
        return gr.skip()
    index = data.get("index")
    if (
        not isinstance(index, (list, tuple))
        or len(index) != 2
        or any(type(value) is not int for value in index)
        or index[0] < 0
        or index[1] != 0
    ):
        return gr.skip()
    value = data.get("value")
    return value if isinstance(value, str) and value.strip() else gr.skip()

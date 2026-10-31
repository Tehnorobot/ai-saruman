"""Run existing offline browser fixture with role-specific streaming usage."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
fixture=(root/'audit/check_dslab_ui.py').read_text(encoding='utf-8')
fixture=fixture.replace("folder=root/'audit/dslab/ui'","folder=root/'audit/planner-budget/ui'")
start=fixture.index('checks = ')
fixture=fixture[:start]+'''mock=mock.replace("totals:{requests:1", "roles:[{role:'planner',requests:1,input_tokens:100,output_tokens:usageOutput,cache_creation_input_tokens:20,cache_read_input_tokens:30,estimated_rub:.1234},{role:'coder',requests:0}],totals:{requests:1")
'''+fixture[start:]
position=fixture.index('\npage=page.replace')
fixture=fixture[:position]+'''\nchecks=checks.replace("await refresh();await refreshUsage();", "await refresh();await refreshUsage();check(q('usagePanel').textContent.includes('Планировщик: 160'), 'planner-role-visible');check(q('usagePanel').textContent.includes('Кодер: 0'), 'coder-role-visible');")
'''+fixture[position:]
exec(compile(fixture,str(root/'audit/check_dslab_ui.py'),'exec'),{'__file__':str(root/'audit/check_dslab_ui.py')})

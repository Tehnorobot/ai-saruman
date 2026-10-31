from pathlib import Path
path=Path(__file__).resolve().parents[1]/'app/index.html'
text=path.read_text(encoding='utf-8')
old='<h2>Тесты и результаты</h2><pre id="verification">'
new='<h2>Тесты и результаты</h2><p class="media-warning" id="acceptanceSummary" role="status">Готовность мода ещё не подтверждена.</p><pre id="verification">'
assert text.count(old)==1
text=text.replace(old,new)
old="q('verification').textContent=JSON.stringify(v,null,2).slice(0,9000);"
assert text.count(old)==1
text=text.replace(old,"q('acceptanceSummary').textContent=acceptanceText(v);"+old)
path.write_text(text,encoding='utf-8')

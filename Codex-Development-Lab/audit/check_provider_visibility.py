"""Independent offline browser checks for actual provider-control visibility."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
phase = sys.argv[1] if len(sys.argv) > 1 else 'after'
assert phase in ('before', 'after')
fixture = (root / 'audit/check_dslab_ui.py').read_text(encoding='utf-8')
checks = r'''<output id="provider-selftest"></output><script>
setTimeout(async()=>{
 const results=[];
 const visible=node=>{const r=node.getBoundingClientRect();return getComputedStyle(node).display!=='none'&&r.width>0&&r.height>0};
 const test=(name,condition,detail)=>results.push({name,status:condition?'PASS':'FAIL',detail});
 try {
  await refresh();
  q('updateSettings').closest('details').open=true;
  for(const [prefix,formId,modelId,effortId] of [['new','form','model','reasoning'],['saved','updateSettings','updateModel','updateReasoning']]){
   const provider=q(prefix+'Provider'),claude=q(prefix+'Claude'),model=q(modelId),effort=q(effortId),form=q(formId);
   const codexLabel=model.closest('label'),claudeLabel=claude.closest('label');
   provider.value='codex';provider.onchange();model.value='gpt-6-sol';effort.value='high';
   test(prefix+'-codex-visible',visible(model)&&visible(codexLabel),getComputedStyle(codexLabel).display);
   test(prefix+'-claude-hidden-for-codex',!visible(claudeLabel)&&!visible(claude),getComputedStyle(claudeLabel).display);
   test(prefix+'-codex-effort-options',Array.from(effort.options).filter(o=>o.value==='max').every(o=>o.hidden)&&Array.from(effort.options).filter(o=>['none','minimal'].includes(o.value)).every(o=>!o.hidden));
   let data=new FormData(form);
   test(prefix+'-codex-payload',data.get('provider')==='codex'&&data.get('model')==='gpt-6-sol'&&data.get('reasoning_effort')==='high',Object.fromEntries(data));
   provider.value='dslab';provider.onchange();claude.value='claude-opus-5-5';claude.onchange();effort.value='max';
   test(prefix+'-claude-visible',visible(claudeLabel)&&visible(claude),getComputedStyle(claudeLabel).display);
   test(prefix+'-codex-hidden-for-claude',!visible(codexLabel)&&!visible(model),getComputedStyle(codexLabel).display);
   test(prefix+'-claude-effort-options',Array.from(effort.options).filter(o=>o.value==='max').every(o=>!o.hidden)&&Array.from(effort.options).filter(o=>['none','minimal'].includes(o.value)).every(o=>o.hidden));
   data=new FormData(form);
   test(prefix+'-claude-payload',data.get('provider')==='dslab'&&data.get('model')==='claude-opus-5-5'&&data.get('reasoning_effort')==='max',Object.fromEntries(data));
   provider.value='codex';provider.onchange();data=new FormData(form);
   test(prefix+'-switchback-model-cleared',visible(model)&&data.get('model')===''&&data.get('reasoning_effort')==='default',Object.fromEntries(data));
   test(prefix+'-switchback-claude-hidden',!visible(claude),getComputedStyle(claudeLabel).display);
  }
  test('fixture-only-read-requests',requests.every(r=>r.method==='GET'),requests);
  q('provider-selftest').textContent=JSON.stringify({status:results.every(x=>x.status==='PASS')?'PASS':'FAIL',checks:results});
 }catch(error){q('provider-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks:results})}
},300);
</script>'''
start = fixture.index('checks = ')
end = fixture.index('\npage=page.replace', start)
fixture = fixture[:start] + 'checks = ' + repr(checks) + fixture[end:]
fixture = fixture.replace("folder=root/'audit/dslab/ui'", "folder=root/'audit/provider-visibility/" + phase + "'")
if phase == 'before':
    fixture = fixture.replace("root/'app/index.html'", "root/'audit/provider-visibility/before/index.html'")
    fixture = fixture.replace("root/'app/provider.js'", "root/'audit/provider-visibility/before/provider.js'")
exec(compile(fixture, str(root/'audit/check_dslab_ui.py'), 'exec'), {'__file__': str(root/'audit/check_dslab_ui.py')})

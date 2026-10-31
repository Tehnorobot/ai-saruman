"""Prefer an executable goal slice after bootstrap, without assigning test verdicts."""
import re
from common import coverage,acceptance_progress

# Conservative recognition, not a semantic requirements parser. Unclear goals
# keep the existing routing. The exact selected source span is always exposed.
GOAL_SECTION=re.compile(r'(?:^| / )(?:цель|goal|objective|purpose)$',re.I)
BEHAVIOR_START=re.compile(r'^\s*(?:реализовать\b|implement\b|(?:аддон|мод|бот|addon|mod|bot)\s+(?:должен\s+)?(?:получает|выращивает|атакует|защищает|выполняет|автоматически\b|plants\b|grows\b|attacks\b|performs\b))',re.I)


def select(catalog,plan,assignment,ledger,source,previous,directive):
    """Schedule only: an executed mapped slice releases the milestone for review."""
    if directive.get('kind')!='normal' or plan.get('notes'):return directive
    checks=previous.get('checks',[]) if isinstance(previous,dict) else []
    if not any(c.get('label')=='gradle_build' and c.get('status')=='PASS' for c in checks):return directive
    if any(c.get('status') in ('FAIL','ERROR','TIMEOUT') for c in checks):return directive
    if assignment.get('unresolved_failures'):return directive
    members=set((assignment.get('current_task') or {}).get('requirement_ids',[]))
    scenarios={s['id']:s for s in plan.get('scenarios',[])}
    rows={r['id']:r for r in plan.get('requirements',[])}
    def executed(sid,passed=False):
        scenario=scenarios.get(sid)
        if not scenario or scenario.get('kind')!='gameplay':return False
        entry=ledger.get('scenarios',{}).get(sid) or {}
        if not isinstance(entry,dict):return False
        record=entry.get('last_pass' if passed else 'latest') or {}
        if not isinstance(record,dict):return False
        result=record.get('result') or {}
        if not isinstance(result,dict):return False
        return (record.get('definition_sha256')==acceptance_progress.definition_digest(scenario)
                and result.get('test_status') in (('PASS',) if passed else ('PASS','FAIL')))
    inventory=coverage.obligations(catalog)
    for requirement in catalog.get('requirements',[]):
        rid=requirement['id']
        if rid not in members or not GOAL_SECTION.search(requirement.get('section','')):continue
        conditions=inventory.get(rid,[])
        candidates=[c for c in conditions if BEHAVIOR_START.search(c['text'])]
        if not candidates:continue
        mapping=rows.get(rid,{}).get('obligation_scenarios',{})
        # Bootstrap must have actual gameplay evidence, not an empty JAR or a
        # planner claim. Source changes may stale it; this is routing, not PASS.
        bootstrap=[sid for c in conditions if c not in candidates
                   for sid in mapping.get(c['id'],[]) if executed(sid,passed=True)]
        if not bootstrap:continue
        for condition in candidates:
            if any(executed(sid) for sid in mapping.get(condition['id'],[])):continue
            return {'kind':'implement_functional_goal','required_target':rid,
                    'required_condition_ids':[condition['id']],
                    'source_condition':condition['text'],
                    'allowed_requirement_ids':[rid],'deferred_scenario':None,'return_due_round':None,
                    'reason':'Совместная загрузка уже проверена. До расширения поставки и новых обзоров нужен минимальный исполняемый срез основного поведения из исходного ТЗ.'}
    return directive

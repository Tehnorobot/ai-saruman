"""Explain independent scenario facts without inferring success or a root cause."""
from __future__ import annotations

import copy
import hashlib
import re
import xml.etree.ElementTree as ET

from common.history import _ids, _open

MAX_TESTS=64
MAX_CASES=512
MAX_REPORT_BYTES=4*1024*1024
MAX_TOTAL_BYTES=16*1024*1024


def _key(value):
    if not isinstance(value,dict):return None
    classname,name=value.get('classname'),value.get('name')
    if not all(isinstance(x,str) and 0<len(x)<=256 for x in (classname,name)):return None
    return classname,name


def _text(value,limit=500):
    return value[:limit] if isinstance(value,str) else ''


def summarize(scenario,result):
    """Describe exactly the expected checks; missing evidence never becomes PASS.

    ``test_cases`` is emitted by the trusted JUnit collector. Historical results
    lacking it stay UNKNOWN unless hydrate() can verify their archived XML.
    """
    proof=result.get('assertion_evidence',{})
    if not isinstance(proof,dict):proof={}
    expected=scenario.get('expected_tests',[])
    expected=expected if isinstance(expected,list) else []
    cases=proof.get('test_cases',[])
    cases=cases if isinstance(cases,list) else []
    grouped={}
    for case in cases[:MAX_CASES]:
        key=_key(case)
        if key is not None:grouped.setdefault(key,[]).append(case)
    failures=proof.get('failures',[])
    failures=failures if isinstance(failures,list) else []
    failed={_key(f):f for f in failures if _key(f) is not None}
    missing=proof.get('missing_tests',[])
    missing={_key(t) for t in missing if _key(t) is not None} if isinstance(missing,list) else set()
    checks=[];known=set()
    for test in expected[:MAX_TESTS]:
        key=_key(test)
        if key is None or key in known:continue
        known.add(key);observed=grouped.get(key,[])
        statuses=[x.get('status') for x in observed]
        report=next((_text(x.get('report'),256) for x in observed if x.get('report')),None)
        if key in failed or 'FAIL' in statuses:
            status='FAIL'
            source=next((x for x in observed if x.get('status')=='FAIL'),failed.get(key,{}))
            reason=_text(source.get('message') or failed.get(key,{}).get('message')) or 'JUnit зафиксировал ошибку этой проверки.'
        elif len(observed)==1 and statuses[0] in ('TEST_PASSED','PASS'):
            status='TEST_PASSED';reason='JUnit зафиксировал успешное выполнение проверки.'
        elif observed and all(s in ('SKIPPED','NOT_TESTED') for s in statuses):
            status='SKIPPED';reason=_text(observed[0].get('message')) or 'JUnit явно пометил проверку пропущенной; причина не указана.'
        elif key in missing and not observed:
            status='NOT_EXECUTED'
            reason='Результат проверки отсутствует в JUnit. Причина не установлена; сценарий мог прерваться раньше.'
        else:
            status='UNKNOWN'
            reason=('JUnit содержит несколько результатов этой проверки; однозначный успех не подтверждён.'
                    if len(observed)>1 else 'В сохранённом отчёте нет подробного результата этой проверки.')
        row={'classname':key[0],'name':key[1],'status':status,'reason':reason,
             'cause_confirmed':False,'source':'junit' if observed or key in failed else 'missing_test_inventory' if key in missing else 'unavailable'}
        if report:row['report']=report
        checks.append(row)
    counts={'total':len(checks),'passed':0,'failed':0,'not_executed':0,'skipped':0,'unknown':0}
    labels={'TEST_PASSED':'passed','FAIL':'failed','NOT_EXECUTED':'not_executed','SKIPPED':'skipped','UNKNOWN':'unknown'}
    for row in checks:counts[labels[row['status']]]+=1
    additional=[]
    for key,case in failed.items():
        if key not in known and len(additional)<8:
            additional.append({'classname':key[0],'name':key[1],'status':'FAIL',
                               'reason':_text(case.get('message')) or 'Дополнительная ошибка JUnit.'})
    return {'schema_version':1,'scenario_id':scenario.get('id',result.get('id')),
            'requirement_ids':list(scenario.get('requirement_ids',[]))[:64],
            'counts':counts,'checks':checks,'additional_failures':additional,
            'observed_failure':_text(result.get('detail'),1000),
            'root_cause_status':'NOT_ESTABLISHED',
            'scope_note':'Это результаты проверок сценария, а не подтверждение выполнения всего пункта ТЗ. Причина ошибки требует отдельной диагностики.',
            'truncated':len(expected)>MAX_TESTS or len(cases)>MAX_CASES}


def enrich_result(scenario,result):
    value=copy.deepcopy(result)
    value['failure_details']=summarize(scenario,value)
    return value


def _xml_cases(raw,relative):
    if b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Unsafe JUnit XML declaration')
    document=ET.fromstring(raw.decode('utf-8-sig'))
    if document.tag not in ('testsuite','testsuites'):raise ValueError('Not a JUnit document')
    rows=[]
    for case in document.iter('testcase'):
        key=_key(case.attrib)
        if key is None:continue
        failure=case.find('failure')
        if failure is None:failure=case.find('error')
        skipped=case.find('skipped')
        status='FAIL' if failure is not None else 'SKIPPED' if skipped is not None else 'TEST_PASSED'
        node=failure if failure is not None else skipped
        message=''
        if node is not None:
            message=node.get('message') or (node.text or '').split('\n',1)[0]
        rows.append({'classname':key[0],'name':key[1],'status':status,
                     'message':message[:500],'report':relative})
        if len(rows)>=MAX_CASES:break
    return rows


def hydrate(artifacts,pid,jid,scenario,result,*,round_number=None):
    """Rebuild details from hash-verified old reports, without changing verdicts.

    Explicit round_number is useful for raw round results lacking artifact_round.
    The operation reads existing files only; it never writes or repairs evidence.
    """
    _ids(pid,jid)
    value=copy.deepcopy(result)
    sid=scenario.get('id')
    if not isinstance(sid,str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',sid) or value.get('id')!=sid:
        raise ValueError('Invalid scenario identity')
    folder=value.get('artifact_round')
    if round_number is not None:
        if type(round_number) is not int or not 0<round_number<=2**53-1:raise ValueError('Invalid artifact round')
        folder=f'round-{round_number:02d}'
    proof=value.get('assertion_evidence',{})
    if not isinstance(proof,dict):proof={}
    value['assertion_evidence']=proof
    reports=proof.get('reports',[])
    if not isinstance(reports,list):reports=[]
    if not isinstance(folder,str) or not re.fullmatch('round-[0-9]{2,16}',folder):
        value=enrich_result(scenario,value);value['failure_details']['evidence_errors']=['Artifact round is unavailable']
        return value
    cases=[];errors=[];total=0
    for report in reports[:64]:
        try:
            if not isinstance(report,dict):raise ValueError('Invalid report descriptor')
            relative,digest=report.get('file'),report.get('sha256')
            if (not isinstance(relative,str) or not re.fullmatch(r'acceptance/'+re.escape(sid)+r'/assertions/report-[0-9]{3}\.xml',relative)
                    or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)):
                raise ValueError('Invalid report path or hash')
            with _open(artifacts,(pid,jid,folder,*relative.split('/'))) as source:
                raw=source.read(MAX_REPORT_BYTES+1)
            total+=len(raw)
            if len(raw)>MAX_REPORT_BYTES or total>MAX_TOTAL_BYTES:raise ValueError('JUnit reports exceed size limit')
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('JUnit report hash mismatch')
            cases.extend(_xml_cases(raw,relative))
        except (OSError,ValueError,UnicodeError,ET.ParseError) as exc:
            if len(errors)<4:errors.append(type(exc).__name__+': '+str(exc)[:160])
        if total>MAX_TOTAL_BYTES or len(cases)>=MAX_CASES:break
    # Do not trust an old persisted test_cases list when its XML cannot be read.
    proof['test_cases']=cases[:MAX_CASES]
    value=enrich_result(scenario,value)
    if len(cases)>=MAX_CASES or len(reports)>64 or total>MAX_TOTAL_BYTES:
        value['failure_details']['truncated']=True
    if errors:value['failure_details']['evidence_errors']=errors
    return value

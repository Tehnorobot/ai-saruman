"""Distinguish a broken verifier from an ordinary project/test failure."""

INFRASTRUCTURE_LABELS=frozenset(('verification_session','verification_report','verification_pipeline'))


def infrastructure_failure(report):
    if (not isinstance(report,dict) or not isinstance(report.get('checks',[]),list)
            or any(not isinstance(c,dict) for c in report.get('checks',[]))):
        return {'label':'verification_report','status':'ERROR','detail':'Malformed verification checks'}
    checks=report.get('checks', [])
    # Prefer the concrete pipeline/report error to its supervisor exit code.
    for check in sorted(checks,key=lambda c:c.get('label')=='verification_session'):
        if (check.get('label') not in INFRASTRUCTURE_LABELS
                or check.get('status') not in ('FAIL','ERROR','TIMEOUT')):
            continue
        # The supervisor intentionally exits 1 after reporting mod/test errors.
        # DockerSandbox.verify also records that exit as verification_session.
        if (check.get('label')=='verification_session' and check.get('status')=='FAIL'
                and check.get('exit_code')==1
                and any(c.get('label') not in INFRASTRUCTURE_LABELS
                        and c.get('status') in ('FAIL','ERROR','TIMEOUT') for c in checks)):
            continue
        return check
    return None

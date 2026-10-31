"""Distinguish legacy aggregate failures from current named-check failures."""
def reconcile_legacy_errors(memory,number,checks):
    if not any(str(c.get('label','')).startswith('scenario:') and c.get('status') in ('PASS','FAIL')
               for c in checks if isinstance(c,dict)):
        return
    for error in memory.get('errors',[]):
        if (error.get('label')=='minecraft_client_gametest' and error.get('status')=='OPEN'
                and error.get('last_round',number)<number):
            error.update(status='HISTORICAL',resolution=
                'Historical aggregate check is not comparable to later named scenarios. '
                'The original failure has not been independently confirmed resolved.')

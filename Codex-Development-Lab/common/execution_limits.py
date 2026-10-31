"""Per-role soft handoff intervals; elapsed time never kills a model turn.

Legacy *_TIMEOUT_SECONDS names remain compatible with existing installations.
They request completion of the current phase, not process termination.
"""
import os


def role_timeout(role, default):
    if role not in ('planner', 'coder', 'reviewer'):
        raise ValueError('Unknown execution role')
    setting = {'planner':'PLANNER_TIMEOUT_SECONDS','reviewer':'REVIEWER_TIMEOUT_SECONDS'}.get(role)
    configured = os.getenv(setting, '').strip() if setting else ''
    if not configured:
        return default
    try:
        value = int(configured)
    except ValueError as exc:
        raise ValueError(setting+' must be an integer >= 60') from exc
    if value < 60:
        raise ValueError(setting+' must be an integer >= 60')
    return value

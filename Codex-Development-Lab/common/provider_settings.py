"""Provider-specific validation. Old jobs continue to use Codex."""
from common.codex_settings import validate_model, validate_reasoning, CODEX_MODELS, CODEX_MODEL_EFFORTS
import json
import re

DSLAB_MODELS = ('claude-sonnet-5-5', 'claude-opus-5-5')
DSLAB_EFFORTS = ('default', 'low', 'medium', 'high', 'xhigh', 'max')
AGENT_ROLES = ('planner', 'coder', 'reviewer')
LITELLM_MODELS = ('deepseek-ai/DeepSeek-V4-Flash-0731', 'Qwen/Qwen3-Next-80B-A3B-Instruct-FP8',
                 'google/gemma-4-26B-A4B-it', 'zai-org/GLM-5.3-Flash', 'Qwen/Qwen3.8-27B')
LITELLM_EFFORTS = {model:('default',) for model in LITELLM_MODELS}
DSLAB_DEFAULT_EFFORTS = {DSLAB_MODELS[0]:'high',DSLAB_MODELS[1]:'medium'}
MINIMAX_MODELS = ('MiniMax-M3.1-Flash',)
MINIMAX_EFFORTS = ('default','low','medium','high','xhigh','max')


def model_catalogue():
    return {'litellm':[{'id':model,'efforts':list(LITELLM_EFFORTS[model])} for model in LITELLM_MODELS],
            'minimax':[{'id':model,'efforts':list(MINIMAX_EFFORTS),'default_effort':'max',
                        'plan':'Explore / M Plan','thinking_required':True} for model in MINIMAX_MODELS],
            'dslab':[{'id':model,'efforts':list(DSLAB_EFFORTS),'default_effort':DSLAB_DEFAULT_EFFORTS[model]}
                     for model in DSLAB_MODELS],
            'codex':[{'id':model,'efforts':list(CODEX_MODEL_EFFORTS[model])} for model in CODEX_MODELS]}


def validate_litellm_model(value):
    if not isinstance(value,str):raise ValueError('LiteLLM requires a model identifier')
    value=value.strip()
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}',value)
            or any(part in ('','..','.') for part in value.split('/'))):
        raise ValueError('Invalid LiteLLM model identifier')
    if value not in LITELLM_MODELS:raise ValueError('Choose a supported LiteLLM model')
    return value


def validate_settings(provider, model, effort, token_budget=1000000, max_output=8192, max_calls=100, *, legacy=False):
    provider = (provider or 'codex').strip()
    if provider not in ('codex', 'dslab', 'litellm', 'minimax'):
        raise ValueError('Unknown provider')
    model = validate_litellm_model(model) if provider=='litellm' else validate_model(model)
    if provider == 'dslab':
        model = model or DSLAB_MODELS[0]
        if model not in DSLAB_MODELS:
            raise ValueError('Choose a supported DSLab Claude model')
        effort = (effort or 'default').strip().lower()
        if effort not in DSLAB_EFFORTS:
            raise ValueError('Unsupported Claude effort')
    elif provider=='minimax':
        model=model or MINIMAX_MODELS[0]
        if model not in MINIMAX_MODELS:raise ValueError('Choose a supported MiniMax Explore model')
        effort=(effort or 'default').strip().lower()
        if effort not in MINIMAX_EFFORTS:raise ValueError('Unsupported MiniMax reasoning effort; thinking cannot be disabled')
    elif provider=='litellm':
        effort=(effort or 'default').strip().lower()
        if legacy:effort='default'
        if effort not in LITELLM_EFFORTS.get(model,('default','none')):
            raise ValueError('Unsupported reasoning setting for the selected LiteLLM model')
    else:
        effort = validate_reasoning(effort)
        if not legacy and model in CODEX_MODEL_EFFORTS and effort not in CODEX_MODEL_EFFORTS[model]:
            raise ValueError('Unsupported reasoning setting for the selected Codex model')
    for value, lower, name in (
        (token_budget, 1000, 'token budget'),
        (max_output, 1024, 'maximum output tokens'),
        (max_calls, 1, 'maximum requests')):
        if type(value) is not int or value < lower:
            raise ValueError('Invalid ' + name)
        # Browser number fields and the existing round-limit API share this
        # exact integer representation range; this is not a spending quota.
        if value > 2**53-1:
            raise ValueError(name+' exceeds the exact integer representation range (2^53-1)')
    if provider == 'dslab' and max_output > token_budget:
        raise ValueError('Output limit exceeds the round token budget')
    return provider, model, effort, token_budget, max_output, max_calls


def validate_role_models(value, *, legacy=False):
    """Explicit stage overrides; inherited defaults never hide provider errors."""
    if value is None:return {}
    if not isinstance(value,dict) or set(value)-set(AGENT_ROLES):
        raise ValueError('Role models support planner, coder and reviewer only')
    normalized={}
    for role, selection in value.items():
        if not isinstance(selection,dict) or set(selection)-{'provider','model','reasoning_effort'}:
            raise ValueError('Invalid role model settings')
        provider=selection.get('provider','codex')
        model=selection.get('model','')
        effort=selection.get('reasoning_effort','default')
        if not all(isinstance(x,str) for x in (provider,model,effort)):
            raise ValueError('Role settings must be text')
        provider,model,effort,*_=validate_settings(provider,model,effort,legacy=legacy)
        normalized[role]={'provider':provider,'model':model,'reasoning_effort':effort}
    return normalized


def resolve_role(job,role):
    if role not in AGENT_ROLES:raise ValueError('Unknown agent role')
    value=job.get('role_models') or {}
    if isinstance(value,str):
        if len(value)>8192:raise ValueError('Role model settings exceed size limit')
        try:value=json.loads(value)
        except (ValueError,RecursionError) as exc:raise ValueError('Invalid stored role model settings') from exc
    selections=validate_role_models(value,legacy=True)
    if role in selections:
        selection=selections[role]
        if selection['provider']=='dslab' and selection['reasoning_effort']=='default':
            selection['reasoning_effort']=DSLAB_DEFAULT_EFFORTS[selection['model']]
        return selection
    # Existing projects did not have a reviewer override. Keep their explicitly
    # selected coder model as the review default, rather than the global model.
    # This is configuration inheritance, never an availability/error fallback.
    if role == 'reviewer':
        return resolve_role(job, 'coder')
    provider,model,effort,*_=validate_settings(job.get('provider','codex'),
        job.get('model',''),job.get('reasoning_effort','default'),legacy=True)
    if provider=='dslab' and effort=='default':effort=DSLAB_DEFAULT_EFFORTS[model]
    return {'provider':provider,'model':model,'reasoning_effort':effort}


def resolve_role_details(job, role):
    """Public effective selection plus the origin shown in the role picker."""
    selection = resolve_role(job, role)
    value = job.get('role_models') or {}
    if isinstance(value, str):
        value = json.loads(value)  # resolve_role already checked the stored data.
    explicit = role in value
    return {**selection, 'inherited': not explicit,
            'inherited_from': None if explicit else ('coder' if role == 'reviewer' else 'job')}

"""Validate model repairs against fresh local evidence and the frozen run token."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import time

from .blocks import AIR, DYNAMIC, MageError, add, block_state, point, require, stable_top, xyz
from .blueprint import DOWN, UP, array, integer, obj, text, topological

SCOPE = ('requestId','runId','generation','schemaRevision','permitGeneration','worldSnapshotRevision')
ACTIONS = {'break_block','place_block','move','observe','scaffold_access'}
TERMINAL = {'STOPPED','BLOCKED','FAILED','READY'}


def close(p,q):
    return sum(abs(a-b) for a,b in zip(p,q))


def matches(observed, block, state, *, dynamic=True):
    return observed is not None and ((block == 'minecraft:air' and observed[0] in AIR) or observed[0] == block) and all((not dynamic and k in DYNAMIC) or observed[1].get(k) == v for k,v in state.items())


@dataclass
class Incident:
    token: dict
    kind: str
    goal: tuple
    expected_block: str
    expected_state: dict
    world: dict
    player: tuple | None
    target_count: int | None
    actions: set
    can_supply: bool
    plan: dict
    current_step: str | None
    deadline: int
    scaffold_stances: set


def parse_request(request):
    obj(request,'expert request')
    token = {k:text(request.get(k),k,max_len=256) for k in ('requestId','runId')}
    token.update({k:integer(request.get(k),k,1 if k=='schemaRevision' else 0,2**63-1) for k in ('generation','schemaRevision','permitGeneration')})
    incident = obj(request.get('incident'),'incident')
    text(incident.get('id'),'incident.id',max_len=256)
    kind = text(incident.get('kind'),'incident.kind',max_len=128)
    for symptom in array(incident.get('symptoms',[]),'symptoms',limit=64):
        text(symptom,'symptom')
    options = obj(request.get('options'),'options')
    continuous = options.get('continuousRecovery') is True
    used = integer(incident.get('attemptsBudgetUsed'),'attemptsBudgetUsed',0,2**31-1 if continuous else 1000)
    if not continuous:
        total = integer(incident.get('attemptsBudgetTotal'),'attemptsBudgetTotal',1,1000)
        require(used<total,'expert incident budget exhausted','incident_budget_exhausted')
    goal = obj(incident.get('goal'),'goal')
    p = xyz(goal.get('cell'))
    block = goal.get('expectedBlock')
    state = block_state(block,goal.get('expectedState'))
    world = {}
    snapshot = obj(request.get('worldSnapshot'),'worldSnapshot')
    xyz(snapshot.get('anchor'),'snapshot anchor')
    revision = text(snapshot.get('revision'),'worldSnapshot.revision',max_len=256)
    require(request.get('worldSnapshotRevision',revision) == revision,'snapshot token disagrees with observation revision','stale_proposal')
    token['worldSnapshotRevision'] = revision
    unknown = {xyz(q) for q in array(snapshot.get('known',[]),'worldSnapshot.known',limit=8192)}
    for c in array(snapshot.get('cells'),'worldSnapshot.cells',limit=8192):
        obj(c,'observed block')
        at = xyz(c)
        require(at not in world and at not in unknown,f'{at}: duplicate or conflicting observation','invalid_expert_request')
        world[at] = (c.get('block'),block_state(c.get('block'),c.get('state'),observed=True))
    ownership = obj(request.get('ownership'),'ownership')
    require(ownership.get('inputOwner') in ('builder','unionclef','foreign','none') and ownership.get('controlHeld') is False,'expert request must not hold game input','invalid_expert_request')
    actions = ACTIONS & {text(a,'available action',max_len=64) for a in array(ownership.get('availableActions'),'availableActions',limit=64)}
    deadline = integer(options.get('deadlineMillis'),'deadlineMillis',0,120000)
    plan = {}
    plan_data = obj(request.get('plan'),'plan')
    current_step = plan_data.get('currentStepId')
    require(current_step is None or isinstance(current_step,str),'invalid currentStepId','invalid_expert_request')
    for row in array(plan_data.get('steps'),'plan.steps',limit=8192):
        obj(row,'work step')
        ident = text(row.get('id'),'work step id',max_len=128)
        require(ident not in plan,f'{ident}: duplicate plan id','invalid_expert_request')
        plan[ident] = row
    # Optional runtime observations already sent by the Java adapter; omitted fields remain unknown.
    player = xyz(snapshot['playerPosition'],'player position') if 'playerPosition' in snapshot else None
    resources = obj(request.get('resources',{}),'resources')
    count = integer(resources['count'],'target item count',0,2**31-1) if 'count' in resources else None
    stances = set()
    for column in array(resources.get('scaffoldAccess', []), 'scaffoldAccess', limit=8):
        stance = xyz(obj(column, 'scaffold access').get('stance'))
        base = xyz(column.get('base'))
        needed = integer(column.get('blocksNeeded'), 'blocksNeeded', 0, 128)
        require(0 < stance[1] - base[1] and (stance[0], stance[2]) == (base[0], base[2])
                and needed <= stance[1] - base[1] and close(stance, p) <= 16
                and base in world and stance in world and stance not in stances,
                'invalid observed scaffold access', 'invalid_expert_request')
        stances.add(stance)
    return Incident(token,kind,p,block,state,world,player,count,actions,resources.get('canSupply') is True,plan,current_step,deadline,stances)


def route_points(values):
    return [xyz(p) for p in array(values,'route',limit=128)]


def route_shape(points):
    return bool(points) and len(points) == len(set(points)) and all(close(p,q)==1 and p[1]==q[1] for p,q in zip(points,points[1:]))


def route_valid(points, supports, world):
    if not route_shape(points):
        return False
    for p in points:
        floor = world.get(add(p,DOWN))
        if not (matches(world.get(p),'minecraft:air',{}) and matches(world.get(add(p,UP)),'minecraft:air',{}) and floor and stable_top(*floor)):
            return False
    return all(matches(world.get(xyz(s)),s['block'],{}) for s in supports)


def select_attempts(request, parsed=None, limit=8):
    """Rank same-run, nearby records; actual successes require current clearance and supports.

    Distinguish a recorded route from an admissible route; retain meaningful negative and
    interrupted evidence without treating a combat displacement as a path obstruction.
    """
    parsed = parsed or parse_request(request)
    ledger = obj(request.get('attemptLedger'),'attemptLedger')
    records = array(ledger.get('records'),'attemptLedger.records',limit=4096)
    candidates = []
    seen = set()
    for record in records:
        if not isinstance(record,dict) or record.get('runId') != parsed.token['runId'] or record.get('type') not in ('attempt','route'):
            continue
        if record['type'] == 'attempt':
            revision = record.get('schemaRevision')
            active = parsed.token['schemaRevision']
            if revision != active and not (request['options'].get('continuousRecovery') is True
                                           and type(revision) is int and 1 <= revision < active):
                continue
        rid = record.get('id')
        if not isinstance(rid,str) or not rid or rid in seen:
            continue
        try:
            goal = xyz(record.get('goal'))
            actual = route_points(record.get('actualRoute',[]) if record['type']=='attempt' else record.get('cells',[]))
            planned = route_points(record.get('plannedRoute',[]))
            supports = array(record.get('supports',[]),'supports',limit=128)
            for s in supports:
                xyz(s); block_state(s.get('block'),observed=True)
        except (MageError, AttributeError):
            continue
        distance = min([close(goal,parsed.goal)]+[close(p,parsed.goal) for p in actual])
        if distance>8:
            continue
        seen.add(rid)
        status = record.get('routeStatus',record.get('status'))
        outcome = record.get('outcome')
        if status == 'interrupted' or outcome in ('cancelled','interrupted'):
            category, reuse = 'interrupted', 'not_blocked'
        elif status == 'working' or outcome == 'confirmed':
            category = 'success'
            reuse = 'valid' if route_valid(actual,supports,parsed.world) else 'invalid_or_unknown'
        elif status == 'blocked' or outcome in ('blocked','failed','timeout','conflict'):
            category = 'failure'
            observed_change = status == 'blocked' and route_valid(planned or actual,supports,parsed.world)
            reuse = 'clearance_now_observed' if observed_change else 'avoid_unchanged_or_unobserved'
        else:
            continue
        tick = record.get('endedAtTick',record.get('observedAtTick',0))
        tick = tick if type(tick) is int else 0
        candidates.append(((distance,-tick,rid),category,record,{'id':rid,'status':reuse,'route':[point(p) for p in (planned or actual if category=='failure' else actual)]}))
    candidates.sort(key=lambda row:row[0])
    chosen = []
    for category in ('success','failure','interrupted'):
        one = next((row for row in candidates if row[1]==category),None)
        if one and len(chosen)<limit:
            chosen.append(one)
    for row in candidates:
        if len(chosen)>=limit:
            break
        if row not in chosen:
            chosen.append(row)
    return [row[2] for row in chosen], [row[3] for row in chosen]


def same_token(proposal, current):
    """Freshness gate to call again at executor application, not just after inference."""
    if not isinstance(proposal,dict) or not isinstance(current,dict) or current.get('state') in TERMINAL | {'PAUSED'}:
        return False
    snap = current.get('worldSnapshot',{})
    if not isinstance(snap,dict):
        return False
    for k in SCOPE:
        val = current.get(k,snap.get('revision') if k=='worldSnapshotRevision' else None)
        other = proposal.get(k)
        if val is None or type(val) is not type(other) or val != other:
            return False
    return current.get('worldSnapshotRevision',snap.get('revision')) == snap.get('revision',current.get('worldSnapshotRevision'))


def step_ids(value,label):
    ids = [text(item,label,max_len=128) for item in array(value,label,limit=64)]
    require(len(set(ids)) == len(ids),f'{label} has duplicates','invalid_proposal')
    return ids


def validate_patch(response, request, parsed=None, evidence=None):
    parsed = parsed or parse_request(request)
    obj(response,'expert response')
    if any(k in response and (type(response[k]) is not type(parsed.token[k]) or response[k]!=parsed.token[k]) for k in SCOPE):
        raise MageError('stale_proposal','provider returned a different request or run token')
    require(response.keys() <= {'outcome','reason','repair','observationRequest','blocked',*SCOPE},'unexpected proposal fields','invalid_proposal')
    outcome = response.get('outcome')
    reason = text(response.get('reason'),'proposal reason',max_len=1024)
    result = {'outcome':outcome,'reason':reason,**parsed.token}
    if outcome == 'request_replan':
        require(request['options'].get('allowReplan') is True and response.keys() <= {'outcome', 'reason', *SCOPE},
                'this incident cannot request a blueprint revision', 'invalid_proposal')
        return result
    tag = {'repair':'repair','request_observation':'observationRequest','blocked':'blocked'}
    require(isinstance(outcome,str) and outcome in tag,'unsupported proposal outcome','invalid_proposal')
    require(all(key == tag[outcome] or key not in response for key in tag.values()),'contradictory outcome payload','invalid_proposal')
    payload = obj(response.get(tag[outcome]),tag[outcome])
    if outcome == 'blocked':
        require(payload.keys() <= {'reason','safeFallback'},'unsupported blocked fields','invalid_proposal')
        detail = text(payload.get('reason'),'blocked reason',max_len=1024)
        fallback = payload.get('safeFallback','none')
        require(fallback=='none','unauthorized fallback','invalid_proposal')
        result['blocked'] = {'reason':detail,'safeFallback':fallback}
        return result
    if outcome == 'request_observation':
        require(payload.keys() <= {'cells','reason'},'unsupported observation fields','invalid_proposal')
        points = route_points(payload.get('cells'))
        require('observe' in parsed.actions and 1<=len(points)<=32 and len(set(points))==len(points) and all(close(p,parsed.goal)<=16 for p in points), 'observation not permitted or not local','invalid_proposal')
        result['observationRequest'] = {'cells':[point(p) for p in points],'reason':text(payload.get('reason'),'observation reason',max_len=1024)}
        return result
    require(payload.keys() <= {'steps','removedStepIds','cleanupIds','route','expects'},'unexpected repair fields','invalid_proposal')
    removed = step_ids(payload.get('removedStepIds',[]),'removedStepIds')
    cleanup = step_ids(payload.get('cleanupIds',[]),'cleanupIds')
    # The baseline plan is immutable on the wire; additional steps may depend on it.
    require(not removed,'cannot remove work-plan steps without an equivalent proof','invalid_proposal')
    route = route_points(payload.get('route',[]))
    if route:
        require('move' in parsed.actions and len(route)<=64 and route_shape(route) and all(close(p,parsed.goal)<=16 for p in route),'route must be local, contiguous and authorized','invalid_proposal')
        require(parsed.player is None or (parsed.player[1]==route[0][1] and close(parsed.player,route[0])<=1),'route cannot be reached from the observed player position','invalid_proposal')
    normalized = []
    inserted = set()
    graph = {}
    for ident,row in parsed.plan.items():
        graph[ident] = step_ids(row.get('dependsOn',[]),'plan dependencies')
    topological(graph)

    def ancestors(children):
        collected = set()
        pending = list(children)
        while pending:
            dep = pending.pop()
            if dep not in collected:
                collected.add(dep)
                pending.extend(graph[dep])
        return collected

    overlay = dict(parsed.world)
    restores = {}
    used_cleanup = set()
    mutated = {}
    moved = None
    scaffold_step = None
    steps = array(payload.get('steps'),'steps',limit=32)
    require(steps,'empty repair','invalid_proposal')
    for row in steps:
        obj(row,'repair step')
        require(row.keys() <= {'id','kind','cells','dependsOn','cleanupIds','note'},'unexpected repair step fields','invalid_proposal')
        ident = text(row.get('id'),'step id',max_len=128)
        kind = row.get('kind')
        require(isinstance(kind,str) and kind in ACTIONS & parsed.actions and ident not in inserted | parsed.plan.keys(),'unknown, duplicate or unauthorized action','invalid_proposal')
        deps = step_ids(row.get('dependsOn',[]),'step dependencies')
        require(set(deps) <= inserted | parsed.plan.keys(),'steps must not depend on future or currently blocked work','invalid_proposal')
        prerequisites = ancestors(deps)
        require(parsed.current_step not in prerequisites,'steps must not depend on future or currently blocked work','invalid_proposal')
        graph[ident] = deps
        inserted.add(ident)
        obligations = step_ids(row.get('cleanupIds',[]),'step cleanupIds')
        require(set(obligations) <= set(cleanup),'undeclared cleanup obligation','invalid_proposal')
        used_cleanup.update(obligations)
        note = row.get('note','')
        require(isinstance(note,str) and len(note)<=1024,'invalid step note','invalid_proposal')
        at = array(row.get('cells'),'step cells',limit=64)
        require(at and (len(at)==1 or kind in ('move','observe')),'invalid number of work-step cells','invalid_proposal')
        clean_cells = []
        coords = []
        for cell in at:
            obj(cell,'step cell')
            require(cell.keys() <= {'x','y','z','block','state'},'invalid step cell fields','invalid_proposal')
            p = xyz(cell)
            require(close(p,parsed.goal)<=16,'repair step is too far from the incident','invalid_proposal')
            coords.append(p)
            clean = point(p)
            if kind in ('break_block','place_block'):
                if p == parsed.goal and scaffold_step:
                    require(scaffold_step in prerequisites, 'goal action must depend on scaffold access', 'invalid_proposal')
                previous = overlay.get(p)
                require(previous is not None,'repair mutates an unknown cell','invalid_proposal')
                require(p not in mutated or mutated[p] in prerequisites,'mutation must depend on the previous change to this cell','invalid_proposal')
                if kind=='break_block':
                    require(previous[0] not in AIR,'cannot break known air','invalid_proposal')
                    block = cell.get('block',previous[0]); state = block_state(block,cell.get('state',previous[1]),observed=True)
                    require(matches(previous,block,state),'break precondition does not match observed state','invalid_proposal')
                    require(p!=parsed.goal or not matches(previous,parsed.expected_block,parsed.expected_state,dynamic=False),'goal already satisfies its static requirements','invalid_proposal')
                    require(p==parsed.goal or obligations,'temporary off-goal opening requires cleanup and exact restoration','invalid_proposal')
                    if p != parsed.goal:
                        require(p not in restores,'temporary opening already outstanding','invalid_proposal')
                        require(previous[0] not in {'minecraft:chest','minecraft:trapped_chest','minecraft:barrel','minecraft:furnace','minecraft:blast_furnace','minecraft:smoker'},'opening cannot promise restoration of unobserved container data','invalid_proposal')
                        restores[p] = (previous,set(obligations))
                    overlay[p] = ('minecraft:air',{})
                else:
                    block = cell.get('block'); state = block_state(block,cell.get('state'))
                    if p == parsed.goal:
                        require(block == parsed.expected_block and state == parsed.expected_state,'repair changes the mandatory goal state','invalid_proposal')
                    else:
                        require(p in restores and restores[p][0] == (block,state) and restores[p][1] == set(obligations),'off-goal placement must restore a declared opening','invalid_proposal')
                        del restores[p]
                    require(previous[0] in AIR,'physical placement target is not empty','invalid_proposal')
                    require(p!=parsed.goal or parsed.target_count!=0 or parsed.can_supply,'no observed material for physical placement','invalid_proposal')
                    overlay[p] = (block,state)
                clean.update(block=block,state=state)
                mutated[p] = ident
            else:
                require('block' not in cell and 'state' not in cell,'move/observe cells must be coordinates','invalid_proposal')
            clean_cells.append(clean)
        if kind == 'scaffold_access':
            require(scaffold_step is None and not route and not obligations and parsed.goal not in mutated
                    and coords[0] in parsed.scaffold_stances,
                    'expected one observed scaffold stance before changing the goal', 'invalid_proposal')
            scaffold_step = ident
        if kind=='move':
            require(coords==route and not moved and route_valid(coords,[],overlay),'move must use the validated route','invalid_proposal')
            path_volume = {q for p in route for q in (p,add(p,UP),add(p,DOWN))}
            path_changes = {mutated[p] for p in path_volume if p in mutated}
            require(path_changes <= prerequisites,'move must depend on the repair that clears its route','invalid_proposal')
            for item in evidence or []:
                historical = route_points(item['route'])
                if historical and route==historical and not path_changes:
                    require(item['status'] not in ('invalid_or_unknown','avoid_unchanged_or_unobserved'),'recorded route no longer admissible','invalid_proposal')
            moved = ident
        if kind=='place_block' and route:
            require(moved in prerequisites and 0<close(route[-1],parsed.goal)<=4,'physical placement must depend on a nearby validated stance','invalid_proposal')
        normalized.append({'id':ident,'kind':kind,'cells':clean_cells,'dependsOn':deps,'cleanupIds':obligations,'note':note})
    topological(graph)
    require((not route or moved) and not restores and used_cleanup == set(cleanup),'unfulfilled route or cleanup obligations','invalid_proposal')
    effect = obj(payload.get('expects'),'repair.expects')
    require(effect.keys() <= {'at','block','state'},'unsupported effect fields','invalid_proposal')
    at = xyz(effect.get('at'))
    block = effect.get('block'); state = block_state(block,effect.get('state'))
    require(at==parsed.goal and at in mutated and not matches(parsed.world.get(at),block,state,dynamic=False) and overlay.get(at)==(block,state),'expected state does not prove a new observable goal effect','invalid_proposal')
    require((block,state)==(parsed.expected_block,parsed.expected_state) or (block=='minecraft:air' and parsed.current_step in parsed.plan),'repair would leave the goal unsatisfied without pending original work','invalid_proposal')
    result['repair'] = {'steps':normalized,'removedStepIds':removed,'cleanupIds':cleanup,'route':[point(p) for p in route],'expects':{'at':point(at),'block':block,'state':state}}
    return result


def expert(request, provider):
    start = time.monotonic()
    parsed = parse_request(request)
    records, evidence = select_attempts(request,parsed)
    bounded = deepcopy(request)
    bounded['attemptLedger']['records'] = records
    raw = provider.expert(bounded,parsed,evidence)
    require(not parsed.deadline or (time.monotonic()-start)*1000 < parsed.deadline,'expert inference exceeded its deadline','provider_timeout')
    proposal = validate_patch(raw,request,parsed,evidence)
    require(same_token(proposal,request),'proposal does not match current request','stale_proposal')
    require(not parsed.deadline or (time.monotonic()-start)*1000 < parsed.deadline,'expert validation exceeded its deadline','provider_timeout')
    return proposal

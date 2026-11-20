"""Pure preparation planner over explicit server observations (no game/network access)."""
from __future__ import annotations

from .blocks import AIR, add, block_state, point, require, stable_top, xyz
from .blueprint import DOWN, UP, array, context_anchor, obj, validate_blueprint, world_coordinates


def plan_site(blueprint, context, observations, *, foundation='minecraft:stone_bricks', fill_depth=8):
    """Return actionable steps in order, or a typed observation/blocked result, without relocating the house.

    Observations have absolute coordinates; this result is advisory, not a substitute for Java's
    live survey/reconciliation. Any unobserved coordinate is unknown, never air.
    """
    validate_blueprint(blueprint, context)
    anchor,bounds = context_anchor(context)
    anchor = add(anchor, xyz(blueprint.get('origin', (0,0,0))))
    cap = context['capabilities'] if 'capabilities' in context else {}
    block_state(foundation)
    require(stable_top(foundation), 'foundation must be stable')
    require(type(fill_depth) is int and 1 <= fill_depth <= 64, 'fill_depth must be 1..64')
    world = {}
    for cell in array(observations,'site observations',limit=200000):
        obj(cell,'observed cell')
        p = xyz(cell)
        require(p not in world, f'duplicate observation at {p}')
        world[p] = (cell.get('block'),block_state(cell.get('block'),cell.get('state'),observed=True))
    cells = {add(xyz(c),anchor):c for c in blueprint['cells']}
    air = {add(xyz(c),anchor) for c in blueprint['requiredAir']}
    walk = {add(xyz(c),anchor) for c in (blueprint['entrance']['outsidePoint'],blueprint['exitPoint'],*blueprint['entrance']['approachCells'])}
    clearance = (air | walk | {add(p,UP) for p in walk}) - cells.keys()
    supports = {add(p,DOWN):'foundation' for p in cells if p[1] == anchor[1]}
    supports.update({add(p,DOWN):'approach_support' for p in walk if add(p,DOWN) not in cells})
    unknown = set()
    blocked = []
    cuts, fill, checked = {}, {}, set()

    def see(p):
        checked.add(p)
        if p not in world:
            unknown.add(p)
        return world.get(p)

    def cut(p, prev, purpose):
        if not cap.get('canBreak',False) and not cap.get('canUseCommands',False):
            blocked.append((p,'clearance_permission_denied'))
        else:
            cuts[p] = {'at':point(p), 'kind':'clear', 'purpose':purpose, 'observed':{'block':prev[0],'state':prev[1]}, 'expects':{'block':'minecraft:air','state':{}}}

    for p in sorted(clearance | cells.keys()):
        world_coordinates(p,(0,0,0),bounds)
        prev = see(p)
        if prev and prev[0] not in AIR:
            target = cells.get(p)
            if target is None or not (target['block'] == prev[0] and all(prev[1].get(k) == v for k,v in target['state'].items())):
                purpose = 'entrance_clearance' if p in walk or add(p,DOWN) in walk else 'building_clearance'
                cut(p,prev,purpose)
    for top,purpose in sorted(supports.items()):
        pending = []
        for n in range(fill_depth+1):
            p = add(top,(0,-n,0))
            if bounds and p[1] < bounds[0]:
                blocked.append((top,'no_stable_foundation')); break
            prev = see(p)
            if not prev:
                break
            if stable_top(*prev):
                if pending and not (cap.get('canPlace',False) or cap.get('canUseCommands',False)):
                    blocked.append((top,'foundation_permission_denied'))
                for q,before in pending:
                    if before[0] not in AIR:
                        cut(q,before,purpose)
                    fill[q] = {'at':point(q), 'kind':'fill', 'purpose':purpose, 'observed':{'block':'minecraft:air','state':{}}, 'expects':{'block':foundation,'state':{}}}
                break
            if n == fill_depth:
                blocked.append((top,'no_stable_foundation')); break
            pending.append((p,prev))
    if blocked:
        return {'state':'BLOCKED','reason':blocked[0][1],'at':point(blocked[0][0]),'steps':[]}
    if unknown:
        return {'state':'UNKNOWN','reason':'site_observation_required','unknown':[point(p) for p in sorted(unknown)],'steps':[]}
    order = lambda p: (p[1],p[0],p[2])
    steps = [cuts[p] for p in sorted(cuts,key=order,reverse=True)] + [fill[p] for p in sorted(fill,key=order)]
    return {'state':'PLANNED' if steps else 'DONE','steps':steps,'surveyedCells':len(checked),'plannedMutations':len(steps)}

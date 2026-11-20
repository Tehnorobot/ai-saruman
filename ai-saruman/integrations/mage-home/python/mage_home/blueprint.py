"""Compile a structured, bounded architectural plan to mage_home.blueprint/1."""
from __future__ import annotations

from collections import Counter, deque
import heapq
import json
import os
from pathlib import Path
import re
import tempfile

from .blocks import AIR, CARPETS, GRAVITY, HORIZONTAL, INTERIOR_DECOR, PAINTINGS, PROPERTIES, MageError, add, block_state, point, require, solid, stable_top, sub, xyz

UP = (0, 1, 0)
DOWN = (0, -1, 0)
KINDS = {'structure', 'furniture', 'decoration', 'site_preparation', 'access'}
WORLD_Y = {'minecraft:overworld': (-64, 319), 'minecraft:the_nether': (0, 255), 'minecraft:the_end': (0, 255)}


def obj(value, label):
    require(isinstance(value, dict), f'{label}: expected an object')
    return value


def array(value, label, *, limit=50000):
    require(isinstance(value, list) and len(value) <= limit, f'{label}: expected an array of at most {limit} entries')
    return value


def text(value, label, *, max_len=8192):
    require(isinstance(value, str) and 0 < len(value.strip()) <= max_len, f'{label}: expected nonempty text')
    return value


def integer(value, label, lo, hi):
    require(type(value) is int and lo <= value <= hi, f'{label}: expected integer {lo}..{hi}')
    return value


def identifier(p):
    return 'cell:' + ','.join(map(str, p))


def context_anchor(context):
    obj(context, 'context')
    world = obj(context.get('world'), 'world')
    if world.get('worldUid') is not None:
        text(world['worldUid'], 'world.worldUid', max_len=640)
    else:
        text(world.get('server'), 'world.server', max_len=512)
    text(world.get('dimension'), 'world.dimension', max_len=128)
    for k, v in obj(context.get('capabilities', {}), 'capabilities').items():
        if k in {'canBreak', 'canPlace', 'canUseItems', 'canUseCommands', 'supportsScaffolding'}:
            require(type(v) is bool, f'capabilities.{k}: expected boolean')
    return xyz(context.get('anchor'), 'anchor'), WORLD_Y.get(world['dimension'])


def world_coordinates(p, anchor, bounds):
    x, y, z = add(p, anchor)
    require(abs(x) <= 29_999_983 and abs(z) <= 29_999_983 and (bounds is None or bounds[0] <= y <= bounds[1]), f'{p}: outside world build limits at {x,y,z}', 'world_bounds')


def topological(entries):
    deps = {k: set(v) for k, v in entries.items()}
    users = {k: [] for k in deps}
    for target, sources in deps.items():
        require(sources <= deps.keys() and target not in sources, f'{target}: unknown/self dependency')
        for source in sources:
            users[source].append(target)
    ready = [k for k, v in deps.items() if not v]
    heapq.heapify(ready)
    result = []
    while ready:
        source = heapq.heappop(ready)
        result.append(source)
        for target in users[source]:
            deps[target].remove(source)
            if not deps[target]:
                heapq.heappush(ready, target)
    require(len(result) == len(entries), 'cyclic dependencies')
    return result


def facing(value, label):
    require(isinstance(value, str) and value in HORIZONTAL, f'{label}: invalid facing')
    return value


def pair_info(cells, pairs):
    membership = set()
    pair_ids = set()
    secondary = set()
    for pair in pairs:
        obj(pair, 'pair')
        pair_id = text(pair.get('id'), 'pair.id')
        kind = pair.get('kind')
        positions = [xyz(p) for p in array(pair.get('cells'), 'pair.cells', limit=2)]
        require(len(positions) == 2 and len(set(positions)) == 2 and all(p in cells and p not in membership for p in positions) and pair_id not in pair_ids, f'{pair_id}: malformed or overlapping pair')
        pair_ids.add(pair_id)
        membership.update(positions)
        p, q = positions
        a, b = cells[p], cells[q]
        sa, sb = a['state'], b['state']
        block = a['block']
        require(block == b['block'] and sa.get('facing') in HORIZONTAL and sa.get('facing') == sb.get('facing'), f'{pair_id}: inconsistent pair orientation or block')
        if kind == 'door' and block.endswith('_door'):
            lower, upper = (p, q) if sa.get('half') == 'lower' else (q, p)
            ls, us = cells[lower]['state'], cells[upper]['state']
            require(add(lower, UP) == upper and ls.get('half') == 'lower' and us.get('half') == 'upper' and ls.get('hinge', 'left') == us.get('hinge', 'left'), f'{pair_id}: invalid door halves')
            secondary.add(upper)
        elif kind == 'bed' and block.endswith('_bed'):
            foot, head = (p, q) if sa.get('part') == 'foot' else (q, p)
            require(add(foot, HORIZONTAL[sa['facing']]) == head and cells[foot]['state'].get('part') == 'foot' and cells[head]['state'].get('part') == 'head', f'{pair_id}: invalid bed halves')
            secondary.add(head)
        elif kind == 'chest' and block in {'minecraft:chest', 'minecraft:trapped_chest'}:
            left, right = (p, q) if sa.get('type') == 'left' else (q, p)
            dx, _, dz = HORIZONTAL[sa['facing']]
            require(add(left, (-dz, 0, dx)) == right and cells[left]['state'].get('type') == 'left' and cells[right]['state'].get('type') == 'right', f'{pair_id}: invalid double-chest halves')
        else:
            raise MageError('invalid_blueprint', f'{pair_id}: unsupported multiblock pair {kind}')
    for p, cell in cells.items():
        block, state = cell['block'], cell['state']
        paired = block.endswith(('_door', '_bed')) or (block in {'minecraft:chest', 'minecraft:trapped_chest'} and state.get('type', 'single') != 'single')
        require(not paired or p in membership, f'{p}: multiblock cell is not paired')
    return secondary


def support_for(p, cell):
    block, state = cell['block'], cell['state']
    if block.endswith('_door'):
        return add(p, DOWN) if state.get('half') == 'lower' else None
    if block.endswith('_bed') or block.endswith('_stairs') or block in GRAVITY | CARPETS | {'minecraft:torch', 'minecraft:soul_torch', 'minecraft:flower_pot'} or cell['kind'] == 'furniture':
        return add(p, DOWN)
    if block in {'minecraft:lantern', 'minecraft:soul_lantern'}:
        return add(p, UP if state.get('hanging') == 'true' else DOWN)
    if block in {'minecraft:ladder', 'minecraft:wall_torch', 'minecraft:soul_wall_torch'}:
        return sub(p, HORIZONTAL[facing(state.get('facing'), block)])
    return None


def report_diagnostics(issues):
    if issues:
        error = MageError(issues[0]['code'], issues[0]['detail'])
        error.diagnostics = issues[:24]
        raise error


def check_support(cells):
    issues = []
    for p, c in cells.items():
        support = support_for(p, c)
        if support is not None:
            if support[1] == -1 and c['kind'] in {'structure', 'access', 'site_preparation'}:
                continue  # the mandatory site survey supplies support below y=0
            under = cells.get(support)
            if not under or not stable_top(under['block'], under['state']):
                issues.append({'code': 'unsupported_cell', 'detail': f'{p}: {c["block"]} needs stable support at {support}'})
        if c['block'] in {'minecraft:chest', 'minecraft:trapped_chest'} and add(p, UP) in cells:
            issues.append({'code': 'inaccessible_furniture', 'detail': f'{p}: chest opening obstructed'})
    return issues


def accessibility(cells, air, entrance, outside, exits, extra_routes=(), access_areas=(), access_levels=()):
    passable_doors = {p for p, c in cells.items() if c['block'].endswith('_door') and c['block'] != 'minecraft:iron_door'}
    door_axes = {p: HORIZONTAL[cells[p]['state']['facing']] for p in passable_doors
                 if cells[p]['state'].get('half') == 'lower'}
    carpets = {p for p, c in cells.items() if c['block'] in CARPETS}
    passable = passable_doors | carpets
    airspace = air | passable
    candidates = {outside, *exits, *extra_routes} | airspace
    external = {outside, *exits, *extra_routes}
    nodes = set()
    stairs = {}
    for p in candidates:
        h = add(p, UP)
        below = cells.get(add(p, DOWN))
        if p in cells and p not in passable or h in cells and h not in passable_doors:
            continue
        if p in external and below is None:
            nodes.add(p)  # explicitly surveyed surface outside the house
        elif p in airspace and h in airspace and below:
            if stable_top(below['block'], below['state']):
                nodes.add(p)
            elif below['kind'] != 'furniture' and below['block'].endswith('_stairs') and below['state'].get('half', 'bottom') == 'bottom' and below['state'].get('shape', 'straight') == 'straight':
                nodes.add(p)
                stairs[p] = HORIZONTAL[facing(below['state'].get('facing'), 'stairs')]
    require(outside in nodes and entrance in nodes, 'entrance has no clear supported approach', 'inaccessible_entrance')
    def neighbors(p):
        for direction in HORIZONTAL.values():
            reverse = tuple(-n for n in direction)
            for dy in (0, 1, -1):
                d = (direction[0], dy, direction[2])
                q = add(p, d)
                if q not in nodes:
                    continue
                if any(door in door_axes and (dy or door_axes[door] not in (direction, reverse)) for door in (p, q)):
                    continue
                high, uphill = (q, direction) if dy > 0 else (p, reverse)
                if dy and stairs.get(high) != uphill:
                    continue
                if dy and add(p if dy > 0 else q, (0, 2, 0)) not in air:
                    continue  # the head rises above the lower stance before entering the higher column
                yield q

    visited = {outside}
    queue = deque([outside])
    while queue:
        for q in neighbors(queue.popleft()):
            if q not in visited:
                visited.add(q)
                queue.append(q)
    require(entrance in visited and all(p in visited for p in (*exits, *extra_routes)), 'entrance, approach or exit is disconnected', 'inaccessible_entrance')
    issues = []
    for p, cell in cells.items():
        if cell['kind'] != 'furniture':
            continue
        state, block = cell['state'], cell['block']
        directions = list(HORIZONTAL.values())
        if block in {'minecraft:furnace', 'minecraft:blast_furnace', 'minecraft:smoker'} and state.get('facing') in HORIZONTAL:
            directions = [HORIZONTAL[state['facing']]]
        if block.endswith('_bed') and state.get('part') == 'head':
            continue
        if not any(add(p, d) in visited for d in directions):
            issues.append({'code': 'inaccessible_furniture', 'detail': f'{p}: {block} is not reachable from the entrance'})
    for name, points in access_areas:
        standing = points & nodes
        missing = standing - visited
        if not standing:
            issues.append({'code': 'inaccessible_room', 'detail': f'{name}: no standing space with enough headroom'})
        elif missing:
            issues.append({'code': 'inaccessible_room', 'detail': f'{name}: part of the area is disconnected at {min(missing)}'})
    flat_nodes = nodes - stairs.keys()
    floor_nodes = {p for p in flat_nodes - external - passable_doors
                   if cells[add(p, DOWN)]['kind'] != 'furniture'}
    entered_levels = {p[1] for p in floor_nodes & visited}
    missing_levels = set(access_levels) - entered_levels
    for y in sorted(missing_levels):
        issues.append({'code': 'inaccessible_room', 'detail': f'floor has no reachable standing space at y={y}'})
    # A single unlabeled block behind a corner or space atop the furniture
    # isn't a circulation zone. Even a one-tile area explicitly declared by
    # the designer is checked above.
    floor_levels = {entrance[1], *access_levels}
    # Imported plans may omit level metadata. A straight stair with a
    # same-height landing identifies a further occupiable floor.
    floor_levels.update(p[1] for p, direction in stairs.items() if add(p, direction) in floor_nodes)
    unnamed_floor = {p for p in floor_nodes - visited if p[1] in floor_levels}
    disconnected = next((p for p in sorted(unnamed_floor) if any(q in unnamed_floor for q in neighbors(p))), None)
    if disconnected is not None:
        issues.append({'code': 'inaccessible_room', 'detail': f'unnamed walking area is disconnected at {disconnected}'})
    report_diagnostics(issues)
    return visited


def available_materials(context):
    """An optional authoritative snapshot. The absence of a snapshot means unknown stock."""
    if 'resources' not in context:
        return None
    resources = obj(context['resources'], 'resources')
    inventory = obj(resources.get('inventory'), 'resources.inventory')
    require(len(inventory) <= 256, 'too many inventory entries')
    supply = resources.get('canSupplyBlocks')
    require(type(supply) is bool, 'resources.canSupplyBlocks: expected boolean')
    return {key: integer(count, f'available {key}', 1, 100000) for key, count in inventory.items()
            if key in PROPERTIES and key not in AIR}, supply


def validate_blueprint(blueprint, context):
    """Check an imported blueprint, returning the coordinates of usable standing cells."""
    obj(blueprint, 'blueprint')
    require(blueprint.get('schema') == 'mage_home.blueprint/1', 'unsupported blueprint schema')
    text(blueprint.get('projectId'), 'projectId', max_len=256)
    integer(blueprint.get('revision'), 'revision', 1, 2**31-1)
    text(blueprint.get('description'), 'description')
    reqs = obj(blueprint.get('requirements'), 'requirements')
    for name in ('mandatory', 'assumptions'):
        for value in array(reqs.get(name), name, limit=128):
            text(value, name)
    require(not array(reqs.get('conflicts'), 'conflicts', limit=128), 'blueprint requirements conflict', 'requirement_conflict')
    size = xyz(blueprint.get('size'), 'size')
    require(all(1 <= k <= 96 for k in size) and size[0]*size[1]*size[2]<=65536, 'size is outside supported executor limits')
    origin = xyz(blueprint.get('origin', point((0, 0, 0))), 'origin')
    anchor, bounds = context_anchor(context)
    anchor = add(anchor, origin)
    world_coordinates((0,0,0), anchor, bounds)
    world_coordinates(tuple(s-1 for s in size), anchor, bounds)

    def within(p, margin=0):
        return all(-margin <= coordinate < span+margin for coordinate, span in zip(p,size))

    cells = {}
    dependencies = {}
    for c in array(blueprint.get('cells'), 'cells', limit=8192):
        obj(c, 'cell')
        p = xyz(c)
        kind = c.get('kind')
        require(within(p, 10 if kind in ('site_preparation','access') else 0) and p not in cells, f'{p}: out of bounds or occupied twice', 'geometry_collision')
        world_coordinates(p, anchor, bounds)
        state = block_state(c.get('block'), c.get('state'))
        require(isinstance(kind, str) and kind in KINDS and c['block'] not in AIR | {'minecraft:water', 'minecraft:lava'}, f'{p}: invalid occupied cell')
        name = text(c.get('id', identifier(p)), 'cell.id')
        require(name not in dependencies, f'duplicate cell id {name}')
        dependencies[name] = [text(d, 'dependency') for d in array(c.get('dependsOn', []), 'dependsOn', limit=128)]
        cells[p] = {**c, 'state': state}
    require(cells, 'empty blueprint')
    topological(dependencies)
    air = set()
    for entry in array(blueprint.get('requiredAir'), 'requiredAir', limit=8192-len(cells)):
        p = xyz(entry)
        require(within(p,10) and p not in cells and p not in air, f'{p}: inconsistent required air', 'geometry_collision')
        world_coordinates(p, anchor, bounds)
        air.add(p)
    clear = {xyz(p) for p in array(blueprint.get('circulationClearance', []), 'circulationClearance', limit=4096)}
    require(clear <= air, 'the staircase and landing clearance must remain required air', 'stair_collision')
    pictures = set()
    for paint in array(blueprint.get('paintings', []), 'paintings', limit=64):
        p = xyz(paint)
        direction = HORIZONTAL[facing(paint.get('facing'), 'painting')]
        backing = cells.get(sub(p, direction))
        require(p in air and p not in clear and p not in pictures and within(p)
                and paint.get('variant') in PAINTINGS and backing and solid(backing['block'], backing['state']),
                f'{p}: a painting needs clear wall space and solid backing', 'unsupported_cell')
        pictures.add(p)
    secondary = pair_info(cells, array(blueprint.get('multiBlockPairs'), 'multiBlockPairs'))
    diagnostics = check_support(cells)
    required_items = Counter(c['block'] for p, c in cells.items() if p not in secondary)
    house = Counter()
    materials_keys = set()
    for record in array(blueprint.get('materials'), 'materials'):
        obj(record, 'material')
        item = text(record.get('item'), 'item')
        block_state(item)
        count = integer(record.get('count'), 'material count', 1, 50000)
        purpose = record.get('purpose')
        require(purpose in ('house', 'equipment') and (item, purpose) not in materials_keys, 'invalid or duplicate material entry')
        materials_keys.add((item, purpose))
        if purpose == 'house':
            house[item] = count
    if house != required_items:
        diagnostics.append({'code': 'invalid_blueprint', 'detail': 'house material counts do not match final cells and pairs'})
    available = available_materials(context)
    if available is not None and not available[1]:
        missing = house - Counter(available[0])
        require(not missing, 'missing building materials: ' + ', '.join(f'{item} ×{n}' for item, n in sorted(missing.items())),
                'materials_unavailable')
    obligations = {}
    for name in ('cleanup', 'restoration'):
        obligations[name] = set()
        for record in array(blueprint.get(name), name):
            obj(record, name)
            p = xyz(record)
            state = block_state(record.get('block'), record.get('state'))
            world_coordinates(p, anchor, bounds)
            require(p not in obligations[name], f'{p}: duplicate {name} obligation')
            obligations[name].add(p)
            if name == 'cleanup':
                require(record.get('purpose') in ('scaffold', 'platform', 'pillar', 'temporary_opening'), 'invalid cleanup purpose')
            else:
                text(record.get('reason'), 'restoration reason')
                target = cells.get(p)
                require((not target or (target['block'] == record['block'] and all(state.get(k) == v for k, v in target['state'].items()))) and (p not in air or record['block'] in AIR), f'{p}: restoration conflicts with final state')
    for p in obligations['cleanup'] & cells.keys():
        require(p in obligations['restoration'], f'{p}: temporary infrastructure would overwrite a final requirement without restoration')
    entry = obj(blueprint.get('entrance'), 'entrance')
    inn, outside, exit_point = xyz(entry), xyz(entry.get('outsidePoint')), xyz(blueprint.get('exitPoint'))
    d = HORIZONTAL[facing(entry.get('facing'), 'entrance')]
    approaches = [xyz(p) for p in array(entry.get('approachCells'), 'approachCells', limit=512)]
    if (plot := context.get('siteBounds')) is not None:
        obj(plot, 'siteBounds')
        width = integer(plot.get('width'), 'siteBounds.width', 3, 96)
        depth = integer(plot.get('depth'), 'siteBounds.depth', 3, 96)
        require(0 <= origin[0] and origin[0] + size[0] <= width and 0 <= origin[2] and origin[2] + size[2] <= depth,
                f'house does not fit the surveyed {width}x{depth} plot', 'site_bounds')
        targets = cells.keys() | air | obligations['cleanup'] | obligations['restoration'] | {outside, exit_point, *approaches}
        for target in targets:
            x, _, z = add(target, origin)
            require(-1 <= x <= width and -1 <= z <= depth,
                    f'approach must stay within one block of the surveyed {width}x{depth} plot', 'site_bounds')
    require(outside == add(inn, d), 'outsidePoint is not adjacent to entrance in its facing direction')
    hints = array(blueprint.get('scaffoldAccess', []), 'scaffoldAccess', limit=8)
    work_columns = set()
    protected_columns = {(x, z) for x, _, z in (*cells, *air, inn, outside, exit_point, *approaches)}
    for hint in hints:
        x, y, z = xyz(hint, 'scaffoldAccess')
        require(y == 0 and within((x, y, z), 4) and (x, z) not in protected_columns | work_columns,
                'scaffoldAccess must be a distinct exterior column outside circulation and the house', 'scaffold_access')
        world_coordinates((x, y, z), anchor, bounds)
        work_columns.add((x, z))
    door = cells.get(inn)
    require(door and door['block'].endswith('_door') and door['state'].get('half') == 'lower' and door['state'].get('facing') == entry['facing'], 'entrance is not a matching lower door')
    for p in (outside, exit_point, *approaches):
        for q in (p, add(p, UP), add(p, DOWN)):
            world_coordinates(q, anchor, bounds)
    areas = []
    names = set()
    for area in array(blueprint.get('accessAreas', []), 'accessAreas', limit=1024):
        obj(area, 'access area')
        name = text(area.get('name'), 'access area name', max_len=256)
        y = integer(area.get('y'), 'access area height', 0, size[1]-1)
        points = {(x, y, z) for x, z in tiles(area.get('area'), 'access area tiles')}
        require(name not in names and points and all(within(p, 0) for p in points), f'{name}: invalid access area')
        names.add(name)
        areas.append((name, points))
    levels = [integer(y, 'access level height', 0, size[1]-1)
              for y in array(blueprint.get('accessLevels', []), 'accessLevels', limit=8)]
    try:
        reachable = accessibility(cells, air, inn, outside, (exit_point,), approaches, areas, levels)
    except MageError as error:
        if not diagnostics:
            raise
        report_diagnostics(diagnostics + getattr(error, 'diagnostics', [{'code': error.code, 'detail': str(error)}]))
    report_diagnostics(diagnostics)
    return reachable


def tile(value, label):
    require(isinstance(value, (list, tuple)) and len(value) == 2 and all(type(i) is int and 0 <= i < 96 for i in value), f'{label}: expected two integer tile indices (x,z) in 0..95')
    return tuple(value)


def tiles(rectangles, label):
    result = set()
    for r in array(rectangles, label, limit=64):
        require(isinstance(r, list) and len(r) == 4, f'{label}: inclusive rectangle [x1,z1,x2,z2] required')
        x1, z1 = tile(r[:2], label)
        x2, z2 = tile(r[2:], label)
        require(x1 <= x2 and z1 <= z2, f'{label}: inverted rectangle')
        result.update((x, z) for x in range(x1, x2+1) for z in range(z1, z2+1))
    return result


def design_state(block, value):
    """Accept model shorthand only in the architecture DSL."""
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith('[') and raw.endswith(']'):
            raw = raw[1:-1].strip()
        props = {}
        for part in raw.split(','):
            pair = re.fullmatch(r'\s*([a-z0-9_]+)\s*=\s*([a-z0-9_]+)\s*', part)
            require(pair is not None and pair[1] not in props, f'{block}: malformed or duplicate BlockState property', 'invalid_block_state')
            props[pair[1]] = pair[2]
        value = props
    if block in {'minecraft:bookshelf', 'minecraft:crafting_table'} and isinstance(value, dict):
        # These full furniture blocks have no visible facing variant.
        if value.get('facing') in tuple(HORIZONTAL):
            value = {key: item for key, item in value.items() if key != 'facing'}
    return block_state(block, value)


def add_required_paintings(blueprint, cells, air, room_reach, requirements, context):
    if not requirements:
        return
    require(context.get('resources', {}).get('canHangPaintings') is True,
            'paintings require the client to hang paintings and obtain the selected variants',
            'painting_supply_unavailable')
    clearance = {xyz(p) for p in blueprint['circulationClearance']}
    sites = {}
    for room, standing in room_reach.items():
        candidates = {}
        for feet in sorted(standing):
            at = add(feet, UP)
            if feet in clearance or at not in air or at in clearance:
                continue
            for face, direction in HORIZONTAL.items():
                wall = cells.get(sub(at, direction))
                if (wall and wall['kind'] == 'structure' and 'glass' not in wall['block']
                        and solid(wall['block'], wall['state'])):
                    candidates[at] = face
                    break
        sites[room] = candidates
    selected = {}
    for quote, count, names in requirements:
        require(all(name in sites for name in names), f'{quote}: painting room is missing', 'requirement_unsatisfied')
        groups = [sites[name] for name in names] if names else [{p: face for sites_in_room in sites.values()
                                                                for p, face in sites_in_room.items()}]
        for group in groups:
            missing = count - sum(at in selected for at in group)
            if missing <= 0:
                continue
            free = [(at, face) for at, face in group.items() if at not in selected]
            require(len(free) >= missing and len(selected) + missing <= 64,
                    f'{quote}: insufficient accessible wall space for the requested paintings', 'requirement_unsatisfied')
            selected.update(free[:missing])
    blueprint['paintings'] = [{**point(at), 'facing': face, 'variant': PAINTINGS[index % len(PAINTINGS)]}
                              for index, (at, face) in enumerate(selected.items())]


def compile_plan(plan, description, context, project_id, *, pitched_default=False):
    """The model supplies architectural intent; no model-generated command or blueprint is executed."""
    obj(plan, 'plan')
    text(description, 'description')
    text(project_id, 'projectId', max_len=256)
    conflicts = array(plan.get('conflicts', []), 'conflicts', limit=128)
    if conflicts:
        require(not conflicts, '; '.join(f'{text(obj(c, "conflict").get("code"), "conflict.code")}: {text(c.get("detail"), "conflict.detail")}' for c in conflicts), 'requirement_conflict')
    assumptions = [text(a, 'assumption') for a in array(plan.get('assumptions', []), 'assumptions', limit=128)]
    palette = obj(plan.get('palette'), 'palette')
    floor, wall, roof = [text(palette.get(k), f'palette.{k}') for k in ('floor', 'wall', 'roof')]
    door_block = text(palette.get('door', 'minecraft:oak_door'), 'palette.door')
    for b in (floor, wall, roof):
        block_state(b)
        require(solid(b), f'{b} must be a stable full construction block')
    require(door_block.endswith('_door'), 'palette.door must be a door')
    cells, air, pairs, rooms, all_doors = {}, set(), [], {}, {}
    access_areas, decorations = [], []

    def put(p, b, s=None, kind='structure'):
        require(len(cells)<8192, 'project exceeds supported cell count')
        state = block_state(b, s)
        require(p not in cells, f'{p}: {b} collides with {cells.get(p, {}).get("block")}', 'geometry_collision')
        cells[p] = {**point(p), 'block': b, 'state': state, 'kind': kind, 'id': identifier(p), 'dependsOn': []}

    levels = array(plan.get('levels'), 'levels', limit=8)
    require(levels, 'at least one level is required')
    heights = []
    bases = []
    layouts = []
    roofs = []
    volume = set()
    windows = list(array(plan.get('windows', []), 'windows', limit=256))
    for i, level in enumerate(levels):
        obj(level, f'level {i}')
        h = integer(level.get('interiorHeight', 2), 'interiorHeight', 2, 6)
        windows.extend(array(level.get('windows', []), 'level windows', limit=256))
        base = (bases[-1]+heights[-1]+1) if i else 0
        heights.append(h)
        bases.append(base)
        footprint = tiles(level.get('footprint'), 'footprint')
        require(footprint, 'empty footprint')
        layouts.append(footprint)
        envelope = {(x, z) for x, z in footprint if any((x+dx, z+dz) not in footprint for dx, _, dz in HORIZONTAL.values())}
        partitions = tiles(level.get('partitions', []), 'partitions')
        require(partitions <= footprint, 'partition outside footprint')
        boundary = envelope | partitions
        for x,z in footprint:
            put((x,base,z), floor)
            for y in range(base+1,base+h+1):
                volume.add((x,y,z))
        doors = {}
        for door in array(level.get('doors', []), 'doors', limit=128):
            obj(door, 'door')
            t = tile(door.get('at'), 'door.at')
            direction = facing(door.get('facing'), 'door')
            hinge = door.get('hinge', 'left')
            require(t in boundary and t not in doors and hinge in ('left', 'right'), f'{t}: door must fit exactly one wall')
            doors[t] = (direction, hinge)
            all_doors[(t[0], base+1, t[1])] = direction
        for x,z in sorted(boundary):
            for y in range(base+1,base+h+1):
                if (x,z) in doors and y <= base+2:
                    f, hinge = doors[(x,z)]
                    put((x,y,z),door_block,{'half': 'lower' if y == base+1 else 'upper', 'facing': f, 'hinge': hinge})
                else:
                    put((x,y,z),wall)
            if (x,z) in doors:
                ps = [(x,base+1,z),(x,base+2,z)]
                pairs.append({'id': 'door:' + identifier(ps[0]), 'kind': 'door', 'cells': [point(p) for p in ps]})
        room_tiles = set()
        for room in array(level.get('rooms'), 'rooms', limit=128):
            obj(room, 'room')
            name = text(room.get('name'), 'room.name')
            typ = text(room.get('type'), 'room.type')
            area = tiles(room.get('area'), 'room.area')
            require(name not in rooms and area and area <= footprint-boundary and not room_tiles & area, f'{name}: invalid, duplicate, or overlapping room')
            room_tiles.update(area)
            rooms[name] = (typ, i, area, [])
            access_areas.append({'name': 'room: ' + name, 'roomType': typ, 'y': base + 1, 'area': room['area']})
        for item in array(level.get('furniture', []), 'furniture', limit=256):
            obj(item, 'furniture')
            x,z = tile(item.get('at'), 'furniture.at')
            name = text(item.get('room'), 'furniture.room')
            require(name in rooms and rooms[name][1] == i and (x,z) in rooms[name][2], f'{x,z}: furniture outside its room')
            p = (x,base+1,z)
            b = text(item.get('block'), 'furniture.block')
            state = design_state(b, item.get('state'))
            require(not b.endswith(('_door','_stairs','_fence')) and b not in AIR,
                    f'{name} at {x,z}: {b} is not a furniture block. Use supported furniture or put stairs in the stairs array.')
            if b.endswith('_bed'):
                d = HORIZONTAL[facing(state.get('facing'), 'bed')]
                q = add(p, d)
                require(state.get('part', 'foot') == 'foot' and (q[0],q[2]) in rooms[name][2], 'bed head crosses room boundary')
                put(p,b,{**state,'part':'foot'},'furniture')
                put(q,b,{**state,'part':'head'},'furniture')
                pairs.append({'id': 'bed:' + identifier(p), 'kind':'bed', 'cells':[point(p),point(q)]})
            else:
                put(p,b,state,'furniture')
            rooms[name][3].append(b)
        for decor in array(level.get('decorations', []), 'decorations', limit=256):
            obj(decor, 'decoration')
            x, dy, z = xyz(decor.get('at'), 'decoration.at')
            name = text(decor.get('room'), 'decoration.room')
            require(name in rooms and rooms[name][1] == i and (x, z) in rooms[name][2] and 0 <= dy <= h,
                    f'{name}: decoration outside the room or its floor')
            block = text(decor.get('block'), 'decoration.block')
            state = design_state(block, decor.get('state'))
            require(solid(block, state) if dy == 0 else block in INTERIOR_DECOR,
                    f'{block}: this decoration is not supported at height {dy}')
            decorations.append(((x, base + dy, z), block, state, dy == 0, name))
        roofs.append(set(footprint))
    roof_plan = obj(plan.get('roof',{}), 'roof')
    top = roofs[-1]
    width = max(x for x, _ in top) - min(x for x, _ in top) + 1
    depth = max(z for _, z in top) - min(z for _, z in top) + 1
    default_gable = pitched_default and len(top) == width * depth and min(width, depth) >= 5
    roof_kind = roof_plan.get('kind', 'gable' if default_gable else 'flat')
    require(roof_kind in ('flat','gable'), 'unsupported roof geometry')
    for i, footprint in enumerate(roofs):
        roof_y = bases[i] + heights[i] + 1
        next_floor = layouts[i+1] if i+1 < len(layouts) else set()
        if roof_kind == 'flat' or i+1 < len(roofs):
            for x,z in footprint-next_floor:
                put((x,roof_y,z),roof)
            continue
        x0,x1 = min(x for x,_ in footprint),max(x for x,_ in footprint)
        z0,z1 = min(z for _,z in footprint),max(z for _,z in footprint)
        axis = roof_plan.get('ridgeAxis', 'x' if default_gable and width > depth else 'z')
        require(axis in ('x','z') and len(footprint)==(x1-x0+1)*(z1-z0+1), 'gable requires a rectangular top level and ridgeAxis x or z')
        step = integer(roof_plan.get('runPerRise',2),'roof.runPerRise',1,4)
        highest = 0
        for x,z in footprint:
            span = min(x-x0,x1-x) if axis=='z' else min(z-z0,z1-z)
            end = z in (z0,z1) if axis=='z' else x in (x0,x1)
            rise = span//step
            highest = max(highest,rise)
            put((x,roof_y+rise,z),roof)
            for yy in range(roof_y,roof_y+rise):
                if end:
                    put((x,yy,z),wall)
                else:
                    volume.add((x,yy,z))
        require(highest>0, 'gable has no ridge above the eaves')
    glazing = {}
    for window in array(windows, 'windows', limit=256):
        obj(window,'window')
        at = xyz(window.get('at'),'window.at')
        existing = cells.get(at)
        require(at not in glazing and existing and existing['kind']=='structure' and existing['block']==wall and any(at[1] in range(base+1,base+height+1) for base,height in zip(bases,heights)), 'window must replace an existing non-door wall cell')
        block = window.get('block','minecraft:glass')
        require(isinstance(block,str) and ('glass' in block or block=='minecraft:iron_bars'), 'unsupported glazing')
        del cells[at]
        put(at,block,design_state(block,window.get('state')))
        glazing[at] = block
    stair_routes = []
    stair_clearance = set()
    for stair in array(plan.get('stairs', []), 'stairs', limit=32):
        obj(stair, 'stair')
        require(len(levels) > 1, 'stairs require a destination level')
        i = integer(stair.get('fromLevel'), 'stair.fromLevel', 0, len(levels)-2)
        f = facing(stair.get('facing'), 'stairs')
        dx, _, dz = d = HORIZONTAL[f]
        x, z = tile(stair.get('at'), 'stair.at')
        rise = bases[i+1]-bases[i]
        stair_block = text(stair.get('block'), 'stair.block')
        require(stair_block.endswith('_stairs'), 'stair.block must be stairs')
        start = (x-dx, bases[i]+1, z-dz)
        landing = (x+rise*dx, bases[i+1]+1, z+rise*dz)
        overhead = add(start, (0, 2, 0))
        if overhead in cells and overhead[1] == bases[i+1] and cells[overhead]['kind'] == 'structure' and cells[overhead]['block'] == floor:
            del cells[overhead]
        stair_clearance.add(overhead)
        for n in range(rise):
            tx,tz = x + n*dx, z + n*dz
            p = (tx,bases[i]+1+n,tz)
            require((tx,tz) in layouts[i] and (tx,tz) in layouts[i+1], 'stairs leave the interior footprint', 'stair_collision')
            clearance = [add(p, UP), add(p, (0, 2, 0))]
            if n < rise - 1:
                clearance.append(add(p, (0, 3, 0)))
            for q in (p, *clearance):
                if q in cells:
                    c = cells[q]
                    require(q[1] == bases[i+1] and c['block'] == floor and c['kind'] == 'structure', f'{q}: staircase collides with {c["block"]}', 'stair_collision')
                    del cells[q]
                volume.add(q)
            stair_clearance.update(clearance)
            for yy in range(bases[i]+1,p[1]):
                q = (tx,yy,tz)
                require(q not in cells, f'{q}: staircase support collides with {cells[q]["block"] if q in cells else "block"}', 'stair_collision')
                put(q,wall)
            put(p,stair_block,{'facing':f,'half':'bottom','shape':'straight','waterlogged':'false'},'access')
        stair_routes.append((i, start, landing))
        stair_clearance.update((start, add(start, UP), landing, add(landing, UP)))
    # Deck and courtyard surfaces are explicit, not inferred from words in the request.
    features = {}
    for deck in array(plan.get('decks', []), 'decks', limit=32):
        obj(deck, 'deck')
        kind = deck.get('type')
        level = integer(deck.get('level'), 'deck.level', 0, len(levels)-1)
        deck_area = tiles(deck.get('area'), 'deck.area')
        rails = tiles(deck.get('rails', []), 'deck.rails')
        y = bases[level]
        p = xyz(deck.get('door'), 'deck.door')
        require(isinstance(kind, str) and kind in {'balcony','courtyard'} and deck_area and rails < deck_area and not deck_area & layouts[level] and p[1] == y+1 and p in all_doors, 'invalid deck, wall overlap, or missing deck door')
        require(kind == 'balcony' and level > 0 or kind == 'courtyard' and level == 0, 'deck level is incompatible with its type')
        direction = HORIZONTAL[all_doors[p]]
        q = add(p,direction)
        require((q[0],q[2]) in deck_area-rails, 'deck door does not open onto its deck')
        for x,z in sorted(deck_area):
            at = (x,y,z)
            if at not in cells:
                put(at,floor,kind='access')
            if (x,z) in rails:
                put((x,y+1,z),deck.get('railBlock','minecraft:oak_fence'),kind='access')
            for dy in range(1, 4 if kind == 'courtyard' else 3):
                require((x,y+dy,z) not in cells or (dy == 1 and (x,z) in rails), 'deck headroom is obstructed')
                volume.add((x,y+dy,z))
        require(kind not in features, f'duplicate {kind}')
        features[kind] = (p, q, deck_area)
        access_areas.append({'name': kind, 'y': y + 1, 'area': deck['area']})
    for at, block, state, floor_accent, name in decorations:
        require(at not in stair_clearance, f'{at}: decoration blocks a staircase or its landing', 'stair_collision')
        if floor_accent:
            original = cells.get(at)
            require(original and original['kind'] == 'structure' and original['block'] == floor,
                    f'{at}: a floor accent needs an intact room floor', 'geometry_collision')
            cells[at] = {**original, 'block': block, 'state': state, 'kind': 'decoration'}
        else:
            put(at, block, state, 'decoration')
        rooms[name][3].append(block)
    obstructed = sorted(stair_clearance & cells.keys())
    require(not obstructed, f'{obstructed[:8]}: keep stair approaches, landings and overhead space clear', 'stair_collision')
    air |= (volume | stair_clearance) - cells.keys()
    outside = obj(plan.get('entrance'), 'entrance')
    inn = xyz(outside.get('door'), 'entrance.door')
    require(inn in all_doors and inn[1] == 1, 'entrance must be a ground-floor door')
    f = all_doors[inn]
    front = add(inn,HORIZONTAL[f])
    require((front[0],front[2]) not in layouts[0], 'entrance does not face out of the house')
    extra = [xyz(p) for p in array(outside.get('approach', []), 'entrance.approach', limit=128)]
    exitp = xyz(outside.get('exit', front), 'entrance.exit')
    if all(p[1] in {0, front[1]} and (p[0],p[2]) not in layouts[0] for p in (*extra, exitp)):
        # Some designers describe the surface blocks at y=0. The wire format
        # needs standing positions at the bottom-door height.
        extra = [(x,front[1],z) for x,_,z in extra]
        exitp = (exitp[0],front[1],exitp[2])
    require(inn in cells, 'missing entrance')
    secondary = pair_info(cells, pairs)
    for p, cell in cells.items():
        dep = support_for(p,cell)
        if dep in cells:
            cell['dependsOn'].append(identifier(dep))
    for pair in pairs:
        p,q = map(xyz,pair['cells'])
        if pair['kind'] in ('bed','door'):
            cells[q]['dependsOn'].append(identifier(p))
    required_items = Counter(c['block'] for p,c in cells.items() if p not in secondary)
    dim = tuple(max(q[i] for q in cells.keys() | air)+1 for i in range(3))
    work_columns = []
    for hint in array(plan.get('scaffoldAccess', []), 'scaffoldAccess', limit=8):
        require(isinstance(hint, list) and len(hint) == 2 and all(type(i) is int for i in hint),
                'scaffoldAccess requires an exterior [x,z] column', 'scaffold_access')
        work_columns.append(point((hint[0], 0, hint[1])))
    bp = {'schema':'mage_home.blueprint/1','projectId':project_id,'revision':1,'templateId':None,'description':description,
          'requirements':{'mandatory':[],'assumptions':assumptions,'conflicts':[]},'size':point(dim),'origin':point((0,0,0)),
          'cells':sorted(cells.values(),key=lambda c:(c['y'],c['z'],c['x'])),
          'requiredAir':[point(p) for p in sorted(air)],'multiBlockPairs':sorted(pairs,key=lambda item:item['id']),
          'accessAreas':access_areas, 'accessLevels':[base + 1 for base in bases],
          'circulationClearance':[point(p) for p in sorted(stair_clearance)],
          'entrance':{**point(inn),'facing':f,'outsidePoint':point(front),'approachCells':[point(p) for p in extra]},'exitPoint':point(exitp),
          'materials':[{'item':b,'count':n,'purpose':'house'} for b,n in sorted(required_items.items())], 'cleanup':[], 'restoration':[]}
    if work_columns:
        bp['scaffoldAccess'] = work_columns
    reachable = validate_blueprint(bp,context)
    room_reach = {}
    for name, (_,i,area,_) in rooms.items():
        room_reach[name] = {(x,bases[i]+1,z) for x,z in area} & reachable
        require(room_reach[name], f'room {name} is disconnected from the entrance', 'inaccessible_room')
    for _, start, landing in stair_routes:
        require(start in reachable and landing in reachable, 'stair flight lacks a reachable start or landing', 'stair_collision')
    for kind,(_, p, _) in features.items():
        require(p in reachable, f'{kind} is not reachable', 'inaccessible_room')
    mandatory = array(plan.get('mandatory'), 'mandatory', limit=128)
    require(mandatory, 'model did not identify any mandatory requirements', 'requirement_unsatisfied')
    painting_requirements = []
    for row in mandatory:
        obj(row, 'mandatory requirement')
        quote = text(row.get('quote'), 'mandatory.quote')
        require(quote in description, f'not traceable to the original description: {quote}', 'requirement_unsatisfied')
        facts = array(row.get('checks'), 'mandatory.checks', limit=32)
        require(facts, 'mandatory quote has no testable conditions', 'requirement_unsatisfied')
        for fact in facts:
            obj(fact, 'requirement check')
            kind = fact.get('kind')
            value = fact.get('value', fact.get('block') if kind in ('furniture', 'palette') else None)
            ok = False
            if kind == 'storeys':
                ok = type(value) is int and len(levels) == value
            elif kind == 'room_count':
                count = integer(value, 'room_count value', 1, 128)
                typ = text(fact['roomType'], 'room_count roomType') if 'roomType' in fact else None
                level = fact.get('level')
                selected = [(name,i,area) for name,(t,i,area,_) in rooms.items() if (typ is None or t == typ) and (level is None or i == level)]
                ok = len(selected) == count
                if ok and fact.get('separate') is True:
                    # A pair of adjacent bare tiles is not a room separation, even if the model named two rooms.
                    for n,(_, i, a) in enumerate(selected):
                        neighbors = {(x+dx,z+dz) for x,z in a for dx,_,dz in HORIZONTAL.values()}
                        ok = ok and all(i != j or not (neighbors & b) for _,j,b in selected[n+1:])
            elif kind == 'furniture':
                b = text(value, 'furniture value')
                names = [text(r,'room name') for r in array(fact.get('rooms'), 'furniture.rooms', limit=128)]
                ok = bool(names) and all(r in rooms and b in rooms[r][3] for r in names)
            elif kind == 'windows':
                count = integer(value, 'windows value', 1, 256)
                block = text(fact['block'], 'windows block') if 'block' in fact else None
                ok = sum((block is None or actual == block) and cells[p]['block'] == actual
                         for p, actual in glazing.items()) >= count
            elif kind == 'paintings':
                count = integer(value, 'paintings value', 1, 64)
                names = [text(room, 'painting room') for room in array(fact.get('rooms', []), 'paintings.rooms', limit=64)]
                painting_requirements.append((quote, count, names))
                ok = True  # Wall space and supplies are checked against the completed layout below.
            elif kind == 'interior_stairs':
                ok = value is True and {i for i,_,_ in stair_routes} == set(range(len(levels)-1)) and len(levels)>1
            elif kind in ('balcony','courtyard'):
                ok = value is True and kind in features
                if ok and fact.get('fromRoom') is not None:
                    p,_,_ = features[kind]
                    room = text(fact['fromRoom'], 'deck source room')
                    ok = room in room_reach and sub(p,HORIZONTAL[all_doors[p]]) in room_reach[room]
                if ok and fact.get('overEntrance') is True:
                    _,q,deck = features[kind]
                    ok = (inn[0],inn[2]) in deck and q[1] > inn[1]
            elif kind == 'layout' and value == 'l_shape':
                fp = layouts[0]
                x1,x2,z1,z2 = min(x for x,_ in fp),max(x for x,_ in fp),min(z for _,z in fp),max(z for _,z in fp)
                missing = {(x,z) for x in range(x1,x2+1) for z in range(z1,z2+1)} - fp
                if missing:
                    mx = {x for x,_ in missing}; mz = {z for _,z in missing}
                    ok = len(missing) == len(mx)*len(mz) and len(mx)==max(mx)-min(mx)+1 and len(mz)==max(mz)-min(mz)+1 and len(mx)<x2-x1+1 and len(mz)<z2-z1+1 and any((x,z) in missing for x in (x1,x2) for z in (z1,z2))
            elif kind == 'roof':
                ok = value == roof_kind
            elif kind == 'palette':
                ok = isinstance(fact.get('element'), str) and fact['element'] in {'floor','wall','roof','door'} and value == {'floor':floor,'wall':wall,'roof':roof,'door':door_block}[fact['element']]
            require(ok, f'{quote}: unsupported or unsatisfied condition {kind!r} ({value!r})', 'requirement_unsatisfied')
        if quote not in bp['requirements']['mandatory']:
            bp['requirements']['mandatory'].append(quote)
    if painting_requirements:
        add_required_paintings(bp, cells, air, room_reach, painting_requirements, context)
        validate_blueprint(bp, context)
    return bp


def save_project(directory, blueprint):
    """Optionally archive a validated snapshot without allowing project IDs in filesystem paths."""
    from hashlib import sha256
    payload = json.dumps(blueprint, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n'
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    file_name = sha256(blueprint['projectId'].encode()).hexdigest() + '.json'
    handle, temporary = tempfile.mkstemp(prefix='.blueprint-', dir=root)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, root / file_name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return root / file_name

"""Fit a varied interior into an already checked shell and its circulation."""

from collections import Counter
from hashlib import sha256
from random import Random
import re

from .blocks import CARPETS, HORIZONTAL, PAINTINGS, MageError, add, point, solid, stable_top, sub, xyz
from .blueprint import UP, DOWN, accessibility, available_materials, identifier, support_for, tiles, validate_blueprint


THEMES = (
    ('spruce', 'brown', 'orange', 'yellow'), ('birch', 'light_gray', 'cyan', 'blue'),
    ('oak', 'green', 'lime', 'brown'), ('dark_oak', 'purple', 'magenta', 'pink'),
    ('cherry', 'pink', 'red', 'white'), ('bamboo', 'yellow', 'orange', 'green'),
)


def wants_interior(description):
    value = description.casefold()
    return bool(re.search(r'интерьер|обстав|обустро|укра[сш]|декор|уютн|furnish|interior|decorate', value)
                and not re.search(r'без\s+(?:мебели|интерьера|обстановки|декора)|пуст[а-я]*\s+(?:дом|комнат)|unfurnished|no furniture', value))


def room_kind(name):
    value = name.casefold()
    for kind, pattern in (
        ('sleep', r'спаль|детск|bedroom|nursery|\bsleep\b'), ('kitchen', r'кух|столов|kitchen|dining'),
        ('work', r'кабинет|мастер|библиот|study|office|librar|workshop'),
        ('storage', r'кладов|склад|storage|pantry'), ('hall', r'прихож|коридор|тамбур|hallway|foyer|corridor'),
    ):
        if re.search(pattern, value):
            return kind
    return 'living'


def furnish(blueprint, context):
    """Preserve the supplied furniture and add only features that still fit the checked plan."""
    rooms = [(a['name'], a.get('roomType', ''), a['y'], {(x, a['y'], z) for x, z in tiles(a['area'], 'room')})
             for a in blueprint.get('accessAreas', ()) if a['name'].startswith('room: ')]
    if not rooms or not wants_interior(blueprint['description']):
        return blueprint

    seed = sha256(blueprint['projectId'].encode()).digest()
    wood, main, line, accent = Random(seed).choice(THEMES)
    cells = {xyz(c): c for c in blueprint['cells']}
    air = {xyz(p) for p in blueprint['requiredAir']}
    fixed_air = {xyz(p) for p in blueprint.get('circulationClearance', ())}
    supply = available_materials(context)
    stock = None if supply is None or supply[1] else Counter(supply[0]) - Counter(
        {m['item']: m['count'] for m in blueprint['materials'] if m['purpose'] == 'house'})
    hang_paintings = context.get('resources', {}).get('canHangPaintings') is True
    wall_art = not re.search(r'без\s+картин|no paintings', blueprint['description'].casefold())
    paintings = list(blueprint.get('paintings', ()))
    picture_positions = {xyz(painting) for painting in paintings}
    fixed_air.update(picture_positions)
    fixed_air.update(add(p, DOWN) for p in picture_positions)
    picture_backings = {sub(xyz(painting), HORIZONTAL[painting['facing']]) for painting in paintings}
    accent_positions = set()
    door = blueprint['entrance']
    entrance, outside, exit_at = xyz(door), xyz(door['outsidePoint']), xyz(blueprint['exitPoint'])
    approach = [xyz(p) for p in door['approachCells']]
    areas = [(a['name'], {(x, a['y'], z) for x, z in tiles(a['area'], 'room')}) for a in blueprint['accessAreas']]
    levels = blueprint['accessLevels']
    materials = Counter()

    def room_access():
        return accessibility(cells, air, entrance, outside, (exit_at,), approach, areas, levels)

    reachable = room_access()
    for p, c in cells.items():
        if c['block'].endswith('_door') and c['state'].get('half') == 'lower':
            step = HORIZONTAL[c['state']['facing']]
            fixed_air.update((add(p, step), sub(p, step)))

    def place(group, *, verify=True):
        nonlocal reachable
        positions = {p for p, *_ in group}
        if len(positions) != len(group) or any(p in fixed_air or p not in air for p in positions):
            return False
        counts = Counter(block for _, block, *_ in group)
        if stock is not None and (counts - stock):
            return False
        new = {}
        for p, block, state, kind, extra in group:
            cell = {**point(p), 'block': block, 'state': state, 'kind': kind, 'id': identifier(p), 'dependsOn': []}
            under = support_for(p, cell)
            if under is not None:
                support = cells.get(under)
                if not support or not stable_top(support['block'], support['state']):
                    return False
                cell['dependsOn'].append(identifier(under))
            if extra is not None:
                cell['dependsOn'].append(identifier(extra))
            new[p] = cell
        cells.update(new)
        air.difference_update(positions)
        try:
            if verify:
                reachable = room_access()
        except MageError:
            for p in positions:
                del cells[p]
            air.update(positions)
            return False
        materials.update(counts)
        if stock is not None:
            stock.subtract(counts)
        return True

    def fixture(p, name, state=None, kind='furniture', extra=None):
        return p, 'minecraft:' + name, state or {}, kind, extra

    def available(area, p):
        return p in area and p in reachable and p in air and p not in fixed_air

    def decorate_wall(edges, directions, random):
        if not wall_art:
            return
        for p in edges:
            art = add(p, UP)
            if art not in air or art in fixed_air or art in picture_positions or p not in reachable:
                continue
            for facing, direction in directions:
                backing = sub(art, direction)
                wall = cells.get(backing)
                if (not wall or wall['kind'] != 'structure' or not solid(wall['block'], wall['state'])
                        or 'glass' in wall['block'] or backing in accent_positions or backing in picture_backings):
                    continue
                if hang_paintings and len(paintings) < 64:
                    paintings.append({**point(art), 'facing': facing, 'variant': random.choice(PAINTINGS)})
                    picture_positions.add(art)
                    picture_backings.add(backing)
                    return
                adjoining = [add(backing, d) for d in HORIZONTAL.values() if d[0] * direction[0] + d[2] * direction[2] == 0]
                for side in adjoining:
                    other = cells.get(side)
                    if (not other or other['block'] != wall['block'] or other['kind'] != 'structure'
                            or side in accent_positions or side in picture_backings):
                        continue
                    colors = [f'minecraft:{color}_wool' for color in (line, accent)]
                    if stock is not None and Counter(colors) - stock:
                        continue
                    for target, color in zip((backing, side), colors):
                        materials[cells[target]['block']] -= 1
                        cells[target] = {**cells[target], 'block': color, 'state': {}, 'kind': 'decoration'}
                        materials[color] += 1
                        accent_positions.add(target)
                    if stock is not None:
                        stock.subtract(colors)
                    return

    for name, room_type, y, area in rooms:
        random = Random(sha256(seed + name.encode() + str(y).encode()).digest())
        kind = room_kind(room_type + ' ' + name)
        positions = sorted(area)
        random.shuffle(positions)
        edges = [p for p in positions if any(add(p, d) not in area for d in HORIZONTAL.values())]
        directions = list(HORIZONTAL.items())
        random.shuffle(directions)
        room_scale = len(area)
        if kind not in ('storage', 'hall') and room_scale >= 8:
            for table in edges + positions:
                if not available(area, table):
                    continue
                fitted = False
                for facing, direction in directions:
                    chair = sub(table, direction)
                    if not available(area, chair):
                        continue
                    seat = fixture(chair, f'{wood}_stairs', {'facing': facing, 'half': 'bottom'})
                    group = [fixture(table, f'{wood}_fence'), fixture(add(table, UP), f'{wood}_slab',
                             {'type': 'bottom'}, 'decoration', table), seat]
                    if place(group):
                        fitted = True
                        break
                if fitted:
                    break

        roles = {
            'sleep': ('bookshelf', 'barrel', 'crafting_table'), 'kitchen': ('furnace', 'smoker', 'barrel', 'crafting_table'),
            'work': ('crafting_table', 'cartography_table', 'smithing_table', 'fletching_table', 'bookshelf'),
            'storage': ('barrel', 'chest', 'bookshelf'), 'hall': ('barrel', 'bookshelf'),
            'living': ('bookshelf', 'barrel', 'crafting_table', 'cartography_table'),
        }
        choices = list(roles[kind])
        random.shuffle(choices)
        existing = {cells[p]['block'] for p in area if p in cells}
        added = 0
        for item in choices:
            if 'minecraft:' + item in existing:
                continue
            for p in edges:
                if not available(area, p):
                    continue
                facing = next((f for f, d in directions if available(area, add(p, d))), None)
                if not facing:
                    continue
                state = {'facing': facing} if item in ('furnace', 'smoker', 'chest', 'barrel') else None
                if place([fixture(p, item, state)]):
                    added += 1
                    break
            if added >= min(4, max(1, room_scale // 12)):
                break

        lights = sum(c['block'] in ('minecraft:lantern', 'minecraft:soul_lantern') for p, c in cells.items()
                     if (p[0], y, p[2]) in area and y <= p[1] <= y + 2)
        for p in edges:
            if lights >= max(1, min(3, room_scale // 14)):
                break
            if available(area, p) and place([fixture(p, 'lantern', {'hanging': 'false'}, 'decoration')]):
                lights += 1
        for p in reversed(edges):
            if available(area, p) and place([fixture(p, 'flower_pot', kind='decoration')]):
                break
        decorate_wall(edges, directions, random)

        # The carpet uses the flat, still reachable floor of each room. Keep critical crossings bare.
        border = set(edges)
        for p in positions:
            if not available(area, p):
                continue
            below = cells.get(add(p, DOWN))
            if below is None or not stable_top(below['block'], below['state']):
                continue
            color = line if p in border else accent if (p[0] + p[2] + y) % 7 == 0 else main
            for value in dict.fromkeys([f'minecraft:{color}_carpet', *(sorted(CARPETS) if stock is not None else ())]):
                if place([(p, value, {}, 'decoration', None)], verify=False):
                    break

    if not materials and not paintings:
        return blueprint
    counts = Counter({m['item']: m['count'] for m in blueprint['materials'] if m['purpose'] == 'house'}) + materials
    result = {**blueprint,
              'cells': sorted(cells.values(), key=lambda c: (c['y'], c['z'], c['x'])),
              'requiredAir': [point(p) for p in sorted(air)],
              'paintings': paintings,
              'materials': [m for m in blueprint['materials'] if m['purpose'] != 'house'] +
              [{'item': item, 'count': count, 'purpose': 'house'} for item, count in sorted(counts.items())]}
    validate_blueprint(result, context)
    return result

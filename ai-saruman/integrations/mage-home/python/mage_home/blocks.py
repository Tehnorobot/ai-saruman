"""Conservative Java 1.21.11 BlockState subset for deterministic construction."""
from __future__ import annotations

import re
from typing import Any


class MageError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(test: Any, message: str, code: str = 'invalid_blueprint') -> None:
    if not test:
        raise MageError(code, message)


HORIZONTAL = {'north': (0, 0, -1), 'east': (1, 0, 0), 'south': (0, 0, 1), 'west': (-1, 0, 0)}
DIRECTIONS = (*HORIZONTAL, 'up', 'down')
BOOL = ('true', 'false')
AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
WOOD = ('oak', 'spruce', 'birch', 'jungle', 'acacia', 'dark_oak', 'mangrove', 'cherry', 'pale_oak', 'bamboo', 'crimson', 'warped')
COLORS = ('white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink', 'gray', 'light_gray', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black')
FULL: set[str] = set()
PROPERTIES: dict[str, dict] = {}


def _register(names, props=None, full=False):
    for name in names:
        block = 'minecraft:' + name
        PROPERTIES[block] = props or {}
        if full:
            FULL.add(block)


_register(('air', 'cave_air', 'void_air', 'glass', 'stone', 'cobblestone', 'mossy_cobblestone', 'stone_bricks', 'mossy_stone_bricks', 'cracked_stone_bricks', 'chiseled_stone_bricks', 'bricks', 'mud_bricks', 'packed_mud', 'mud', 'dirt', 'coarse_dirt', 'gravel', 'sand', 'red_sand', 'podzol', 'rooted_dirt', 'andesite', 'diorite', 'granite', 'polished_andesite', 'polished_diorite', 'polished_granite', 'polished_deepslate', 'cobbled_deepslate', 'deepslate_bricks', 'deepslate_tiles', 'sandstone', 'smooth_sandstone', 'red_sandstone', 'smooth_red_sandstone', 'quartz_block', 'smooth_quartz', 'blackstone', 'polished_blackstone', 'polished_blackstone_bricks', 'obsidian', 'terracotta', 'bookshelf', 'crafting_table', 'smithing_table', 'fletching_table', 'cartography_table', 'gold_block', 'iron_block', 'diamond_block', 'calcite', 'tuff', 'polished_tuff', 'tuff_bricks', 'basalt', 'smooth_stone', 'snow_block'), full=True)
_register(('grass_block', 'mycelium', 'podzol'), {'snowy': BOOL}, full=True)
_register(('bedrock', 'netherrack', 'end_stone', 'end_stone_bricks', 'purpur_block', 'clay', 'soul_soil', 'smooth_basalt'), full=True)
_register((f'{w}_planks' for w in WOOD), full=True)
_register((f'{c}_{t}' for c in COLORS for t in ('wool', 'terracotta', 'concrete', 'stained_glass')), full=True)
LOG_WOOD = tuple(w for w in WOOD if w not in ('bamboo', 'crimson', 'warped'))
_register((f'{p}{w}_{t}' for p in ('', 'stripped_') for w in LOG_WOOD for t in ('log', 'wood')), {'axis': ('x', 'y', 'z')}, full=True)
_register((f'{p}{w}_{t}' for p in ('', 'stripped_') for w in ('crimson', 'warped') for t in ('stem', 'hyphae')), {'axis': ('x', 'y', 'z')}, full=True)
_register(('deepslate', 'basalt', 'polished_basalt', 'quartz_pillar', 'hay_block', 'bamboo_block', 'stripped_bamboo_block'), {'axis': ('x', 'y', 'z')}, full=True)
_register((w + '_door' for w in (*WOOD, 'iron')), {'facing': HORIZONTAL, 'half': ('lower', 'upper'), 'hinge': ('left', 'right'), 'open': BOOL, 'powered': BOOL})
_register((c + '_bed' for c in COLORS), {'facing': HORIZONTAL, 'part': ('head', 'foot'), 'occupied': BOOL})
SHAPED = (*WOOD, 'bamboo_mosaic', 'stone', 'cobblestone', 'mossy_cobblestone', 'stone_brick', 'mossy_stone_brick', 'brick', 'mud_brick', 'andesite', 'diorite', 'granite', 'polished_andesite', 'polished_diorite', 'polished_granite', 'polished_deepslate', 'cobbled_deepslate', 'deepslate_brick', 'deepslate_tile', 'sandstone', 'smooth_sandstone', 'red_sandstone', 'smooth_red_sandstone', 'quartz', 'smooth_quartz', 'blackstone', 'polished_blackstone', 'polished_blackstone_brick', 'tuff', 'polished_tuff', 'tuff_brick', 'prismarine', 'prismarine_brick', 'dark_prismarine', 'purpur', 'end_stone_brick', 'nether_brick', 'red_nether_brick')
_register((b + '_stairs' for b in SHAPED), {'facing': HORIZONTAL, 'half': ('top', 'bottom'), 'shape': ('straight', 'inner_left', 'inner_right', 'outer_left', 'outer_right'), 'waterlogged': BOOL})
_register((b + '_slab' for b in (*SHAPED, 'smooth_stone', 'cut_sandstone', 'cut_red_sandstone', 'petrified_oak')), {'type': ('bottom', 'top', 'double'), 'waterlogged': BOOL})
_register((b + '_fence' for b in (*WOOD, 'nether_brick')), {**dict.fromkeys(HORIZONTAL, BOOL), 'waterlogged': BOOL})
_register(('glass_pane', 'iron_bars', *(c + '_stained_glass_pane' for c in COLORS)), {**dict.fromkeys(HORIZONTAL, BOOL), 'waterlogged': BOOL})
_register(('chest', 'trapped_chest'), {'facing': HORIZONTAL, 'type': ('single', 'left', 'right'), 'waterlogged': BOOL})
_register(('furnace', 'blast_furnace', 'smoker'), {'facing': HORIZONTAL, 'lit': BOOL}, full=True)
_register(('barrel',), {'facing': DIRECTIONS, 'open': BOOL}, full=True)
_register(('lantern', 'soul_lantern'), {'hanging': BOOL, 'waterlogged': BOOL})
_register(('ladder',), {'facing': HORIZONTAL, 'waterlogged': BOOL})
_register(('torch', 'soul_torch'))
_register(('wall_torch', 'soul_wall_torch'), {'facing': HORIZONTAL})
CARPETS = {f'minecraft:{color}_carpet' for color in COLORS}
_register((f'{color}_carpet' for color in COLORS))
_register(('flower_pot',))
LIGHTS = {'minecraft:lantern', 'minecraft:soul_lantern', 'minecraft:torch', 'minecraft:soul_torch',
          'minecraft:wall_torch', 'minecraft:soul_wall_torch'}
INTERIOR_DECOR = CARPETS | LIGHTS | {'minecraft:flower_pot'}
PAINTINGS = tuple('minecraft:' + name for name in ('alban', 'aztec', 'aztec2', 'bomb', 'kebab', 'plant', 'wasteland'))
_register(('scaffolding',), {'bottom': BOOL, 'distance': tuple(map(str, range(8))), 'waterlogged': BOOL})
_register(('water', 'lava'), {'level': tuple(map(str, range(16)))})
_register((w + '_leaves' for w in LOG_WOOD), {'distance': tuple(map(str, range(1, 8))), 'persistent': BOOL, 'waterlogged': BOOL})
_register(('short_grass', 'fern', 'dead_bush', 'vine', 'snow', 'tall_grass', 'large_fern'))
PROPERTIES['minecraft:vine'] = dict.fromkeys((*HORIZONTAL, 'up'), BOOL)
PROPERTIES['minecraft:snow'] = {'layers': tuple(map(str, range(1, 9)))}
for _id in ('minecraft:tall_grass', 'minecraft:large_fern'):
    PROPERTIES[_id] = {'half': ('lower', 'upper')}
FULL.difference_update(AIR | {'minecraft:mud', 'minecraft:gravel', 'minecraft:sand', 'minecraft:red_sand'})
GRAVITY = {'minecraft:gravel', 'minecraft:sand', 'minecraft:red_sand'}
DYNAMIC = {'open', 'powered', 'occupied', 'lit'}
VANILLA = re.compile(r'minecraft:[a-z0-9_]+\Z')


def block_state(block: Any, state: Any = None, *, observed: bool = False) -> dict[str, str]:
    """Validate target states against the bounded registry; observations may contain other vanilla blocks."""
    require(isinstance(block, str) and VANILLA.fullmatch(block), 'expected a vanilla block identifier', 'invalid_block_state')
    state = {} if state is None else state
    require(isinstance(state, dict), 'state must be a property object', 'invalid_block_state')
    if observed:
        # Arbitrary vanilla terrain is safely representable as an observation, never as a new target.
        require(all(isinstance(k, str) and re.fullmatch(r'[a-z0-9_]+', k) and isinstance(v, str) and re.fullmatch(r'[a-z0-9_]+', v) for k, v in state.items()), 'invalid observed properties', 'invalid_block_state')
        return dict(sorted(state.items()))
    require(block in PROPERTIES, f'unsupported block {block}', 'invalid_block_state')
    for key, value in state.items():
        require(key in PROPERTIES[block] and isinstance(value, str) and value in PROPERTIES[block][key], f'{block} does not accept {key}={value!r}', 'invalid_block_state')
    if state.get('type') == 'double' and block.endswith('_slab'):
        require(state.get('waterlogged') != 'true', 'a double slab cannot be waterlogged', 'invalid_block_state')
    return dict(sorted(state.items()))


def solid(block: str, state: dict | None = None) -> bool:
    return block in FULL or (block.endswith('_slab') and (state or {}).get('type') == 'double')


def stable_top(block: str, state: dict | None = None) -> bool:
    state = state or {}
    return solid(block, state) or (block.endswith('_slab') and state.get('type') == 'top') or (block.endswith('_stairs') and state.get('half') == 'top')


def add(p, d):
    return tuple(a + b for a, b in zip(p, d))


def sub(p, d):
    return tuple(a - b for a, b in zip(p, d))


def point(p):
    return dict(zip('xyz', p))


def xyz(value, label='point'):
    if isinstance(value, dict):
        values = (value.get('x'), value.get('y'), value.get('z'))
    elif isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        values = ()
    require(len(values) == 3 and all(type(a) is int and abs(a) <= 30_000_000 for a in values), f'{label}: expected integer x,y,z', 'invalid_coordinates')
    return values

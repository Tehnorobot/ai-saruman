import copy
import json
from pathlib import Path
import unittest

from mage_home import MageError, compile_plan, plan_site, validate_blueprint
from mage_home.blocks import block_state, point, xyz
from mage_home.interior import furnish

ROOT = Path(__file__).resolve().parents[1]
REQUEST = json.loads((ROOT/'replay/request-courtyard.json').read_text(encoding='utf-8'))
CASES = json.loads((ROOT/'replay/designs.json').read_text(encoding='utf-8'))


def compile_case(index=0, mutate=None, context=None):
    case = CASES[index]
    plan = copy.deepcopy(case['response'])
    if mutate:
        mutate(plan)
    return compile_plan(plan,case['description'],context or REQUEST['context'],REQUEST['projectId'])


class BlueprintTests(unittest.TestCase):
    def test_scaffold_preferences_keep_the_final_entrance_clear(self):
        blueprint = compile_case(mutate=lambda plan: plan.update(scaffoldAccess=[[-1, 3], [9, 2]]))
        self.assertEqual(blueprint['scaffoldAccess'], [point((-1, 0, 3)), point((9, 0, 2))])
        self.assertEqual(blueprint['cells'], compile_case()['cells'])
        with self.assertRaisesRegex(MageError, 'outside circulation'):
            compile_case(mutate=lambda plan: plan.update(scaffoldAccess=[[2, -1]]))

    def test_courtyard_geometry_against_independent_layers(self):
        bp = compile_case()
        self.assertEqual(bp,compile_case())
        self.assertEqual(bp['description'],REQUEST['description'])
        self.assertEqual(bp['projectId'],REQUEST['projectId'])
        boundary_x = ({0,1,2,3,4,5,6,7,8},{0,4,8},{0,4,8},{0,4,8},{0,1,2,3,5,6,7,8},{0,4},{0,4},{0,1,2,3,4})
        expected = {(x,y,z):(block,{}) for z in range(8) for x in range(9 if z<=4 else 5)
                    for y,block in ((0,'spruce_planks'),(3,'dark_oak_planks'))}
        expected.update({(x,y,z):('stone_bricks',{}) for z,xs in enumerate(boundary_x) for x in xs for y in (1,2)})
        for x,z,f in ((2,0,'north'),(4,2,'east'),(3,4,'south'),(4,6,'east')):
            for y,half in ((1,'lower'),(2,'upper')):
                expected[(x,y,z)] = ('oak_door',{'facing':f,'half':half,'hinge':'left'})
        expected.update({(7,1,1):('crafting_table',{}),(7,1,2):('chest',{'facing':'west','type':'single','waterlogged':'false'}),
                         (1,1,6):('red_bed',{'facing':'east','part':'foot'}),(2,1,6):('red_bed',{'facing':'east','part':'head'}),(5,0,6):('spruce_planks',{})})
        observed = {xyz(c):(c['block'].removeprefix('minecraft:'),c['state']) for c in bp['cells']}
        self.assertEqual(observed,expected)
        free = {(x,y,z) for z in range(8) for x in range(9 if z<=4 else 5) for y in (1,2)} - expected.keys()
        self.assertEqual({xyz(c) for c in bp['requiredAir']},free | {(5,y,6) for y in (1,2,3)})
        self.assertEqual(len(observed),195)
        self.assertEqual(bp['size'],{'x':9,'y':4,'z':8})
        self.assertEqual(bp['exitPoint'],{'x':2,'y':1,'z':-1})
        self.assertEqual({m['item']:m['count'] for m in bp['materials']}['minecraft:oak_door'],4)
        self.assertTrue(all(q in REQUEST['description'] for q in bp['requirements']['mandatory']))
        self.assertTrue(bp['requirements']['assumptions'])

    def test_family_has_two_distinct_bedrooms_stairs_balcony_and_furniture(self):
        bp = compile_case(1)
        visited = validate_blueprint(bp,REQUEST['context'])
        self.assertEqual(bp['size'],{'x':9,'y':8,'z':9})
        self.assertEqual((len(bp['cells']),len(bp['requiredAir'])),(427,187))
        self.assertIn((3,5,1), visited)  # balcony
        self.assertIn((2,5,6), visited)  # top stair landing
        self.assertIn((5,5,7), visited)  # independent east bedroom
        self.assertEqual(sum(p['kind']=='bed' for p in bp['multiBlockPairs']),2)
        self.assertEqual({c['y'] for c in bp['cells'] if c['block']=='minecraft:birch_stairs'},{1,2,3,4})
        self.assertNotEqual(bp['size'],compile_case()['size'])

    def test_stair_ascent_reserves_space_above_the_lower_foot_position(self):
        bp = compile_case(1)
        clearance = {xyz(p) for p in bp['circulationClearance']}
        reserved = next(p for p in sorted(clearance) if p[1] == 4)
        self.assertIn(reserved, {xyz(p) for p in bp['requiredAir']})
        bp['requiredAir'] = [row for row in bp['requiredAir'] if xyz(row) != reserved]
        with self.assertRaisesRegex(MageError, 'clearance must remain required air'):
            validate_blueprint(bp, REQUEST['context'])

    def test_selected_plot_checks_real_blueprint_and_exterior_approach(self):
        context = {**REQUEST['context'], 'siteBounds':{'width':9, 'depth':8}}
        bp = compile_case(context=context)
        context['siteBounds']['width'] = 8
        with self.assertRaisesRegex(MageError, 'does not fit the surveyed 8x8 plot'):
            validate_blueprint(bp, context)
        context['siteBounds']['width'] = 9
        bp['entrance']['approachCells'].append(point((2,1,-2)))
        with self.assertRaisesRegex(MageError, 'one block of the surveyed 9x8 plot'):
            validate_blueprint(bp, context)

    def test_furnishing_keeps_circulation_and_varies_with_new_projects(self):
        source = compile_case(1)
        source = {**source, 'description': source['description'] + ' Decorate the interior.'}
        context = {**REQUEST['context'], 'resources': {'inventory': {}, 'canSupplyBlocks': True, 'canHangPaintings': True}}
        first = furnish(source, context)
        second = furnish({**source, 'projectId': 'other-project'}, context)
        self.assertEqual(first, furnish(source, context))
        self.assertNotEqual(first['cells'], second['cells'])
        self.assertGreater(sum(c['block'].endswith('_carpet') for c in first['cells']), 20)
        self.assertTrue(any(c['kind'] == 'furniture' and c['block'].endswith('_stairs') for c in first['cells']))
        self.assertTrue(any(c['kind'] == 'decoration' and c['block'].endswith('_slab') for c in first['cells']))
        self.assertTrue(first['paintings'])
        self.assertTrue({xyz(p) for p in source['circulationClearance']} <= {xyz(p) for p in first['requiredAir']})
        self.assertTrue(validate_blueprint(first, context))
        finite = {**context, 'resources': {'inventory': {m['item']: m['count'] for m in source['materials']},
                                          'canSupplyBlocks': False, 'canHangPaintings': False}}
        self.assertEqual(furnish(source, finite), source)

    def test_requested_paintings_are_compiled_and_survive_furnishing(self):
        case = CASES[1]
        plan = copy.deepcopy(case['response'])
        room = plan['levels'][0]['rooms'][0]['name']
        quote = 'Put two paintings in this room.'
        description = case['description'] + ' ' + quote
        plan['mandatory'].append({'quote': quote, 'checks': [{'kind': 'paintings', 'value': 2, 'rooms': [room]}]})
        context = {**REQUEST['context'], 'resources': {'inventory': {}, 'canSupplyBlocks': True, 'canHangPaintings': True}}
        blueprint = compile_plan(plan, description, context, 'required-paintings')
        self.assertEqual(len(blueprint['paintings']), 2)
        self.assertIn(quote, blueprint['requirements']['mandatory'])
        furnished = furnish({**blueprint, 'description': description + ' Decorate the interior.'}, context)
        self.assertEqual(furnished['paintings'][:2], blueprint['paintings'])
        self.assertTrue(validate_blueprint(furnished, context))
        with self.assertRaisesRegex(MageError, 'require the client to hang paintings'):
            compile_plan(plan, description, REQUEST['context'], 'unsupported-paintings')

    def test_novel_gable_roof_and_glazing_compile_from_plan_intent(self):
        def modify(p):
            p['roof'] = {'kind':'gable','ridgeAxis':'z','runPerRise':2}
            p['windows'] = [{'at':[8,5,5],'block':'minecraft:glass'}]
        bp = compile_case(1,modify)
        cells = {xyz(c):c['block'] for c in bp['cells']}
        self.assertEqual(bp['size']['y'],10)
        self.assertEqual(cells[(8,5,5)],'minecraft:glass')
        self.assertEqual(cells[(4,9,6)],'minecraft:blue_terracotta')
        self.assertNotIn((4,8,6),cells)
        self.assertIn((4,8,6),{xyz(c) for c in bp['requiredAir']})
        self.assertEqual(cells[(4,8,8)],'minecraft:mud_bricks')

    def test_rectangular_house_defaults_to_gable_and_honors_explicit_flat(self):
        case = CASES[1]
        plan = copy.deepcopy(case['response'])
        arguments = (case['description'], REQUEST['context'], 'roof-choice')
        plan.pop('roof', None)
        raised = compile_plan(plan, *arguments, pitched_default=True)
        self.assertGreater(raised['size']['y'], 8)
        self.assertTrue(validate_blueprint(raised, REQUEST['context']))
        plan['roof'] = {'kind': 'flat'}
        flat = compile_plan(plan, *arguments, pitched_default=True)
        self.assertEqual(flat['size']['y'], 8)
        self.assertEqual(compile_plan(CASES[0]['response'], CASES[0]['description'], REQUEST['context'],
                                      REQUEST['projectId'], pitched_default=True), compile_case())

    def test_model_serialization_preserves_bed_window_and_exit(self):
        def modify(plan, window_count):
            level = plan['levels'][0]
            bed = next(item for item in level['furniture'] if item['block'] == 'minecraft:red_bed')
            bed['state'] = 'facing=east'
            table = next(item for item in level['furniture'] if item['block'] == 'minecraft:crafting_table')
            table['state'] = 'facing=west'
            level['windows'] = [{'at':[0,2,1],'block':'minecraft:glass'}]
            plan['entrance'].update(approach=[[2,0,-2]], exit=[2,0,-3])
            plan['mandatory'].append({'quote':CASES[0]['description'],'checks':[
                {'kind':'windows','value':window_count,'block':'minecraft:glass'},
                {'kind':'room_count','value':len(level['rooms'])},
                {'kind':'furniture','block':'minecraft:red_bed','rooms':[bed['room']]},
                {'kind':'palette','block':plan['palette']['wall'],'element':'wall'},
            ]})
        blueprint = compile_case(mutate=lambda p: modify(p, 1))
        cells = {xyz(cell):cell for cell in blueprint['cells']}
        self.assertEqual(cells[(0,2,1)]['block'],'minecraft:glass')
        self.assertEqual(cells[(1,1,6)]['state'],{'facing':'east','part':'foot'})
        self.assertEqual(cells[(7,1,1)]['state'],{})
        self.assertEqual(blueprint['exitPoint'],{'x':2,'y':1,'z':-3})
        with self.assertRaisesRegex(MageError, 'unsatisfied condition.*windows'):
            compile_case(mutate=lambda p: modify(p, 2))

    def test_semantically_invalid_state_and_world_height(self):
        for block,state in [('minecraft:oak_stairs',{'facing':'up'}),('minecraft:stone_bricks',{'axis':'y'}),('minecraft:oak_door',{'half':'top'}),('minecraft:made_up_bricks',{}),('minecraft:oak_slab',{'type':'double','waterlogged':'true'}),('minecraft:furnace',{'lit':True})]:
            with self.subTest(block=block,state=state),self.assertRaises(MageError):
                block_state(block,state)
        context = copy.deepcopy(REQUEST['context']); context['anchor']['y'] = 318
        with self.assertRaisesRegex(MageError,'outside world build limits'):
            compile_case(context=context)

    def test_disconnected_rooms_colliding_stairs_bad_pairs_and_materials(self):
        with self.assertRaises(MageError) as err:
            compile_case(1,lambda p:p['levels'][1]['doors'].pop())
        self.assertIn(err.exception.code,{'inaccessible_room','inaccessible_furniture'})
        with self.assertRaises(MageError) as err:
            compile_case(1,lambda p:p['stairs'][0].update(at=[4,2]))
        self.assertEqual(err.exception.code,'stair_collision')
        bad = compile_case()
        bed = next(c for c in bad['cells'] if c['state'].get('part')=='head')
        bed['state']['facing'] = 'north'
        with self.assertRaisesRegex(MageError,'inconsistent pair'):
            validate_blueprint(bad,REQUEST['context'])
        bad = compile_case(); bad['materials'][0]['count'] += 1
        with self.assertRaisesRegex(MageError,'material counts'):
            validate_blueprint(bad,REQUEST['context'])

    def test_furniture_cannot_leave_part_of_a_named_room_disconnected(self):
        def restore_blocking_bed(plan):
            plan['levels'][1]['furniture'][0]['state']['facing'] = 'north'
        with self.assertRaisesRegex(MageError, 'room: west: part of the area is disconnected'):
            compile_case(1, restore_blocking_bed)

    def test_unnamed_area_requires_a_real_doorway_and_carpet_remains_walkable(self):
        context = REQUEST['context']
        description = 'House'
        hall = {'name':'hall', 'type':'hall', 'area':[[1,1,3,7]]}
        level = {'footprint':[[0,0,8,8]], 'partitions':[[4,1,4,7]], 'rooms':[hall],
                 'doors':[{'at':[2,0], 'facing':'north'}]}
        plan = {'palette':{'floor':'minecraft:oak_planks','wall':'minecraft:stone_bricks','roof':'minecraft:oak_planks'},
                'levels':[level], 'entrance':{'door':[2,1,0]},
                'mandatory':[{'quote':'House','checks':[{'kind':'storeys','value':1}]}]}
        with self.assertRaisesRegex(MageError, 'unnamed walking area is disconnected'):
            compile_plan(plan, description, context, 'access')
        door = {'at':[4,4], 'facing':'north'}
        level['doors'].append(door)
        with self.assertRaisesRegex(MageError, 'unnamed walking area is disconnected'):
            compile_plan(plan, description, context, 'access')
        door['facing'] = 'east'
        level['decorations'] = [{'room':'hall', 'at':[3,1,4], 'block':'minecraft:red_carpet'}]
        blueprint = compile_plan(plan, description, context, 'access')
        reachable = validate_blueprint(blueprint, context)
        self.assertIn((3,1,4), reachable)
        self.assertIn((6,1,6), reachable)

    def test_decor_uses_supported_materials_and_keeps_headroom(self):
        def decorate(plan):
            level = plan['levels'][0]
            level['interiorHeight'] = 3
            level['decorations'] = [
                {'room':'hall','at':[1,0,1],'block':'minecraft:birch_planks'},
                {'room':'hall','at':[2,1,1],'block':'minecraft:blue_carpet'},
                {'room':'hall','at':[2,3,2],'block':'minecraft:lantern','state':{'hanging':'true'}},
                {'room':'hall','at':[1,1,3],'block':'minecraft:flower_pot'},
            ]
        design = compile_case(mutate=decorate)
        cells = {xyz(c):c for c in design['cells']}
        self.assertEqual(cells[(1,0,1)]['kind'], 'decoration')
        self.assertEqual(cells[(2,3,2)]['state'], {'hanging':'true'})
        self.assertIn(cells[(2,4,2)]['id'], cells[(2,3,2)]['dependsOn'])
        context = copy.deepcopy(REQUEST['context'])
        stock = {row['item']:row['count'] for row in design['materials']}
        context['resources'] = {'inventory':stock,'canSupplyBlocks':False}
        self.assertIn((2,1,1), validate_blueprint(design, context))
        del stock['minecraft:blue_carpet']
        with self.assertRaisesRegex(MageError, 'missing building materials: minecraft:blue_carpet'):
            validate_blueprint(design, context)
        context['resources']['canSupplyBlocks'] = True
        self.assertIn((2,1,1), validate_blueprint(design, context))

    def test_unsatisfied_untraceable_and_conflicting_requirements(self):
        for modify,pattern in [
            (lambda p:p['mandatory'][0]['checks'][0].update(value=2),'unsatisfied'),
            (lambda p:p['mandatory'][0].update(quote='A different prompt'),'not traceable'),
            (lambda p:p.update(conflicts=[{'code':'floors','detail':'Two and one are incompatible'}]),'incompatible'),
        ]:
            with self.subTest(pattern=pattern),self.assertRaisesRegex(MageError,pattern):
                compile_case(mutate=modify)

    def test_import_rejects_collision_cycle_and_bad_support(self):
        bp = compile_case()
        bp['requiredAir'].append(point(xyz(bp['cells'][0])))
        with self.assertRaisesRegex(MageError,'required air'):
            validate_blueprint(bp,REQUEST['context'])
        bp = compile_case(); a,b = bp['cells'][:2]
        a['dependsOn'],b['dependsOn'] = [b['id']],[a['id']]
        with self.assertRaisesRegex(MageError,'cyclic'):
            validate_blueprint(bp,REQUEST['context'])
        bp = compile_case(); next(c for c in bp['cells'] if xyz(c)==(1,0,6))['block']='minecraft:oak_fence'
        with self.assertRaisesRegex(MageError,'stable support'):
            validate_blueprint(bp,REQUEST['context'])


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.bp = compile_case()
        self.context = copy.deepcopy(REQUEST['context'])
        # Complete independent observed cuboid of flat solid terrain under the project and entrance.
        self.world = {(x,y,z): 'minecraft:stone' if y <= -61 or (y==-60 and z==19) else 'minecraft:air'
                      for x in range(9,20) for y in range(-64,-55) for z in range(19,29)}

    def solve(self):
        return plan_site(self.bp,self.context,[{**point(p),'block':b,'state':{}} for p,b in self.world.items()])

    def test_flat_and_uneven_observed_site(self):
        self.assertEqual(self.solve()['state'],'DONE')
        self.world.update({(10,-61,20):'minecraft:water',(10,-62,20):'minecraft:air',(12,-60,19):'minecraft:air',(12,-59,19):'minecraft:oak_log',(15,-58,23):'minecraft:stone'})
        plan = self.solve()
        self.assertEqual(plan['state'],'PLANNED')
        self.assertEqual([s['kind'] for s in plan['steps']],['clear']*3+['fill']*3)
        self.assertEqual([xyz(s['at']) for s in plan['steps'][-3:]],[(10,-62,20),(10,-61,20),(12,-60,19)])
        self.assertIn('entrance_clearance',{s['purpose'] for s in plan['steps']})
        self.assertIn('approach_support',{s['purpose'] for s in plan['steps']})

    def test_unknown_and_permissions_not_mutations(self):
        self.world.pop((10,-61,20))
        plan = self.solve()
        self.assertEqual(plan['state'],'UNKNOWN'); self.assertEqual(plan['steps'],[])
        self.context['capabilities'].update(canBreak=False,canUseCommands=False)
        self.world[(12,-59,19)]='minecraft:oak_log'
        plan = self.solve()
        self.assertEqual(plan['reason'],'clearance_permission_denied'); self.assertEqual(plan['steps'],[])

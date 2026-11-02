"""Static validation and scenario evaluation. This is not the EU5 engine."""
from pathlib import Path
import re
import json
import itertools

BASE = next((p for p in Path(__file__).resolve().parents if (p/'eu5_reference').is_dir()), Path(__file__).resolve().parents[1])
MOD = BASE / 'deliverables/rise_of_serbia'
REF = BASE / 'eu5_reference'
TOKEN = re.compile(r'\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|\?=|!=|<=|>=|[{}=<>]|[^\s{}=<>!?#"]+')

def parse(text):
    tokens = [m.group() for m in TOKEN.finditer(text.lstrip('\ufeff')) if not m.group().isspace() and not m.group().startswith('#')]
    pos = 0
    def group(nested=False):
        nonlocal pos
        nodes = []
        while pos < len(tokens):
            token = tokens[pos]; pos += 1
            if token == '}':
                assert nested, 'Unexpected }'
                return nodes
            if token == '{':
                nodes.append((None, None, group(True)))
                continue
            if pos < len(tokens) and tokens[pos] in ['=','?=','!=','<=','>=','<','>']:
                op = tokens[pos]; pos += 1
                assert pos < len(tokens), 'Missing right operand'
                val = tokens[pos]; pos += 1
                if val == '{': val = group(True)
                nodes.append((token,op,val))
            else:
                nodes.append((None,None,token))
        assert not nested, 'Missing }'
        return nodes
    return group()

def children(nodes, key):
    return [v for k,o,v in nodes if k == key]

def one(nodes,key):
    found = children(nodes,key)
    assert len(found)==1, (key,len(found))
    return found[0]

def walk(nodes):
    for k,o,v in nodes:
        yield k,o,v
        if isinstance(v,list): yield from walk(v)

asts = {}
for path in MOD.rglob('*'):
    if path.suffix in ['.txt','.gui']:
        asts[path.relative_to(MOD).as_posix()] = parse(path.read_text(encoding='utf-8-sig'))

metadata = json.loads((MOD/'.metadata/metadata.json').read_text())
assert metadata['game_id']=='eu5' and metadata['supported_game_version']=='1.4.6'
locfile = MOD/'main_menu/localization/english/ros_l_english.yml'
assert locfile.read_bytes().startswith(b'\xef\xbb\xbf')
localization = dict(re.findall(r'^ ([\w.]+): "(.*)"$',locfile.read_text(encoding='utf-8-sig'),re.M))
assert len(localization)==len(re.findall(r'^ [\w.]+:',locfile.read_text(encoding='utf-8-sig'),re.M)), 'Duplicate loc'
definitions = {k for ast in asts.values() for k,o,v in ast if k}
for name, ast in asts.items():
    for k,o,v in walk(ast):
        if k and k.startswith('ros_') and v in ['yes','no'] if isinstance(v,str) else False:
            assert k in definitions, ('Undefined scripted trigger/effect',k,name)
        if k in ['title','desc','localization_key','text'] and isinstance(v,str):
            key = v.strip('"')
            if key.startswith('ros') and key not in definitions:
                assert key in localization, ('Missing localization',key,name)
        if isinstance(v,str):
            for typ,key in re.findall(r'\b(situation|price):([\w]+)',v):
                if key.startswith('ros_'): assert key in definitions, (typ,key,name)

base_events = parse((REF/'in_game/events/DHE/flavor_ser.txt').read_text(encoding='utf-8-sig'))
mod_events = asts['in_game/events/DHE/flavor_ser.txt']
for k,o,v in base_events:
    if k and k.startswith('flavor_ser.') and int(k.split('.')[1]) not in [10,11,12,30,31,50,260]:
        assert one(mod_events,k)==v, 'Unrelated vanilla event changed: '+k
for event_id in [10,11,12,260]:
    trig = one(one(mod_events,f'flavor_ser.{event_id}'),'trigger')
    assert any(k=='NOT' and ('has_variable','=','ros_managed') in v for k,o,v in trig)
for event_id in [30,31,50]:
    assert not children(one(mod_events,f'flavor_ser.{event_id}'),'dynamic_historical_event')
assert 'location:thessaloniki' not in str(one(mod_events,'flavor_ser.30'))
disaster = asts['in_game/common/disasters/curse_of_stefan_uros_iii.txt']
assert 'ros_managed' in str(one(one(disaster,'curse_of_stefan_uros_iii'),'can_start'))

for phase in ['rise','inheritance']:
    assert f'in_game/gui/panels/situation/ros_{phase}.gui' in asts
    ast = asts[f'in_game/common/situations/ros_{phase}.txt']
    assert children(one(ast,'ros_'+phase),'outcome')

# An interpreter for the narrow effect/trigger subset used by monthly calculations.
effect = one(asts['in_game/common/scripted_effects/ros_effects.txt'],'ros_refresh_realm')
actions = asts['in_game/common/generic_actions/ros_actions.txt']
regions = ['macedonia','albania','epirus','thessaly']
def scalar(v,state):
    if isinstance(v,list):
        out = 0
        for k,o,x in v:
            n = scalar(x,state)
            if k=='value': out=n
            elif k=='multiply': out*=n
            elif k=='min': out=max(out,n)
            elif k=='max': out=min(out,n)
            else: raise AssertionError(k)
        return out
    if v.startswith('var:'): return state[v[4:]]
    try: return float(v)
    except ValueError: return v

def condition(nodes,state):
    for k,o,v in nodes:
        if k in ['scope:actor','c:SER']:
            good=condition(v,state)
        elif k=='NOT': good=not condition(v,state)
        elif k=='OR': good=any(condition([x],state) for x in v)
        elif k=='has_variable': good=v in state
        elif k=='ros_no_delegations': good=not any('ros_'+r+'_delegated' in state for r in regions)
        elif k=='ros_all_held_regions_settled': good=all(not state.get('ros_'+r+'_controlled',False) or 'ros_'+r+'_settled' in state for r in regions)
        elif k.startswith('ros_') and k.endswith('_controlled'): good=state.get(k,False)==(v=='yes')
        elif k=='has_policy': good=v in state.get('policies',[])
        else:
            lhs=state.get(k[4:] if k.startswith('var:') else k)
            rhs=True if v=='yes' else False if v=='no' else scalar(v,state)
            good={'=':lambda:lhs==rhs,'>=':lambda:lhs>=rhs,'<=':lambda:lhs<=rhs,'<':lambda:lhs<rhs,'>':lambda:lhs>rhs}[o]()
        if not good: return False
    return True

def execute(nodes,state):
    last_if = None
    for k,o,v in nodes:
        if k=='set_variable':
            state[one(v,'name')]=scalar(one(v,'value'),state)
        elif k=='change_variable':
            name=one(v,'name')
            for operation in ['add','subtract']:
                if children(v,operation): state[name]+=scalar(one(v,operation),state)*(1 if operation=='add' else -1)
        elif k=='if':
            last_if = condition(one(v,'limit'),state)
            if last_if: execute([x for x in v if x[0]!='limit'],state)
        elif k=='else':
            assert last_if is not None
            if not last_if: execute(v,state)
            last_if=None
        elif k=='c:SER': execute(v,state)
        elif k=='ros_refresh_realm': execute(effect,state)
        else: raise AssertionError(k)

def state(**extra):
    s={'stability':50,'legitimacy':80,'average_control_in_home_region':0.6,'at_war':False,'policies':['law_of_the_pious_emperor'],'country_rank':'country_rank:rank_empire'}
    s.update(extra)
    return s

central = state(ros_successor_prepared=True,ros_stable_months=36)
for r in regions: central['ros_'+r+'_controlled']=True;central['ros_'+r+'_settled']=True
execute(effect,central)
assert central['ros_conquests']==4 and central['ros_strain']==5
assert condition(one(one(actions,'ros_confirm_crown'),'allow'),central)

# Centralization must also be possible without Dušan having prepared the heir.
unprepared = dict(central);unprepared.pop('ros_successor_prepared');execute(effect,unprepared)
assert unprepared['ros_strain']==20
assert condition(one(one(actions,'ros_confirm_crown'),'allow'),unprepared)

compact = dict(central,ros_compact=True)
for r in regions: compact['ros_'+r+'_delegated']=True
execute(effect,compact)
assert compact['ros_delegations']==4 and compact['ros_strain']==22
assert condition(one(one(actions,'ros_confirm_compact'),'allow'),compact)
assert not condition(one(one(actions,'ros_confirm_crown'),'allow'),compact)

bad = dict(compact,stability=0,legitimacy=30,at_war=True,average_control_in_home_region=0.3)
execute(effect,bad)
assert not condition(one(one(actions,'ros_confirm_compact'),'allow'),bad)
assert bad['ros_strain']>compact['ros_strain']

unsettled = dict(central);unsettled.pop('ros_epirus_settled');execute(effect,unsettled)
assert unsettled['ros_unsettled']==1
assert not condition(one(one(actions,'ros_confirm_crown'),'allow'),unsettled)

# All four objectives are not compulsory: a smaller consolidated empire can win.
smaller = dict(central);smaller['ros_epirus_controlled']=False;smaller.pop('ros_epirus_settled');execute(effect,smaller)
assert smaller['ros_conquests']==3
assert condition(one(one(actions,'ros_confirm_crown'),'allow'),smaller)

monthly = one(one(asts['in_game/common/situations/ros_inheritance.txt'],'ros_inheritance'),'on_monthly')
clock = dict(central,ros_stable_months=0,ros_inheritance_months=0)
for _ in range(35): execute(monthly,clock)
assert clock['ros_stable_months']==35 and clock['ros_inheritance_months']==35
assert not condition(one(one(actions,'ros_confirm_crown'),'allow'),clock)
execute(monthly,clock)
assert condition(one(one(actions,'ros_confirm_crown'),'allow'),clock)
clock['at_war']=True
execute(monthly,clock)
assert clock['ros_stable_months']==0 and clock['ros_inheritance_months']==37

# Exercise strain bounds across combinations of economic/political conditions.
for delegated,unsettled_n,prepared,code,compact_on,poor in itertools.product(range(5),range(5),[False,True],[False,True],[False,True],[False,True]):
    s=state(stability=0 if poor else 50,legitimacy=30 if poor else 80,at_war=poor,average_control_in_home_region=0.3 if poor else 0.6)
    s['policies']=['law_of_the_pious_emperor'] if code else []
    if prepared:s['ros_successor_prepared']=True
    if compact_on:s['ros_compact']=True
    for i,r in enumerate(regions):
        if i<delegated:s['ros_'+r+'_delegated']=True
        if i<unsettled_n:s['ros_'+r+'_controlled']=True
    execute(effect,s)
    assert 0<=s['ros_strain']<=100

report={'status':'PASS','script_and_gui_files_parsed':len(asts),'localization_keys':len(localization),
        'checks':['balanced and tokenized scripts and GUI','metadata and UTF-8 BOM','internal script and localization references','unrelated Serbian events unchanged','overlapping disaster and event guards','both imperial endings reachable','unprepared successor can centralize','crisis conditions block premature victory','smaller empire can consolidate','monthly progress reaches 36 and resets on war','400 political combinations stay within strain bounds'],
        'limitations':['EU5 executable unavailable; no engine parsing or in-game UI render performed','province groups verified against scripts; exact 1348 location borders need map/setup files','installation and all-DLC runtime compatibility need user playtest']}
(MOD/'VALIDATION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))

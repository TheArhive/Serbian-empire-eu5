from pathlib import Path
import json
import re
import os
import hashlib

MOD = Path(__file__).resolve().parents[1]
REF = Path(os.environ.get('EU5_REFERENCE_DIR', str(MOD / 'eu5_reference'))).resolve()
LOC = {}

def write(path, text, bom=False):
    dest = MOD / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text.strip() + '\n', encoding='utf-8-sig' if bom else 'utf-8')

def loc(key, value):
    LOC[key] = value

def block_span(text, key, start=0):
    match = re.search(r'(?m)^\s*' + re.escape(key) + r'\s*=\s*\{', text[start:])
    if not match:
        raise ValueError(key)
    opening = start + match.end() - 1
    depth = 1
    quoted = False
    escaped = False
    comment = False
    for i in range(opening + 1, len(text)):
        ch = text[i]
        if comment:
            if ch == '\n': comment = False
            continue
        if quoted:
            if escaped: escaped = False
            elif ch == '\\': escaped = True
            elif ch == '"': quoted = False
            continue
        if ch == '#': comment = True
        elif ch == '"': quoted = True
        elif ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0: return start + match.start(), opening, i + 1
    raise ValueError('Unclosed block: ' + key)

# Province definitions are identifiers verified in the supplied 1.4.6 scripts.
# V0.1 uses province groups, not a claim to a location-perfect 1348 border.
REGIONS = {
    'macedonia': ('Macedonia', ['upper_macedonia_province', 'central_macedonia_province', 'lower_macedonia_province']),
    'albania': ('Albania', ['albania_province']),
    'epirus': ('Epirus and Acarnania', ['north_epirus_province', 'south_epirus_province', 'aetolia_province']),
    'thessaly': ('Thessaly', ['thessaly_province']),
}

def region_location_trigger(region):
    provinces = REGIONS[region][1]
    return 'OR = { ' + ' '.join('province_definition = province_definition:' + p for p in provinces) + ' }\nNOT = { this = location:durres }'

triggers = '''# Country scope unless explicitly marked location scope.
ros_founder_rules = { character:ser_stefan_dusan = { is_alive = yes is_ruler = yes owner = c:SER } }
ros_coronation_ready = { at_war = no owns = location:skopje ros_macedonia_controlled = yes NOT = { country_rank = country_rank:rank_empire } }
ros_code_ready = {
    at_war = no country_rank = country_rank:rank_empire
    OR = { ros_founder_rules = yes ruler ?= { adm > 80 } ruler ?= { has_trait = lawgiver } }
}
ros_all_held_regions_settled = {
'''
for region in REGIONS:
    triggers += f'    OR = {{ ros_{region}_controlled = no has_variable = ros_{region}_settled }}\n'
triggers += '}\nros_no_delegations = {\n'
for region in REGIONS:
    triggers += f'    NOT = {{ has_variable = ros_{region}_delegated }}\n'
triggers += '}\n'
for region, (_, provinces) in REGIONS.items():
    triggers += f'\nros_{region}_location = {{ # Location scope\n{region_location_trigger(region)}\n}}\n'
    triggers += f'ros_{region}_controlled = {{\n'
    for province in provinces:
        triggers += f'''    province_definition:{province} = {{
        NOT = {{ any_ownable_location_in_province_definition = {{
            NOT = {{ this = location:durres }}
            NOT = {{ c:SER = {{ owns_or_non_sovereign_subject_owns = prev }} }}
        }} }}
    }}
'''
    triggers += '}\n'
write('in_game/common/scripted_triggers/ros_triggers.txt', triggers)

# Preserve vanilla events; modify only their entry points and explicit hooks.
source = (REF / 'in_game/events/DHE/flavor_ser.txt').read_text(encoding='utf-8-sig')
patched = source
for event_id in [10, 11, 12, 260]:
    a, _, b = block_span(patched, f'flavor_ser.{event_id}')
    event = patched[a:b]
    _, opening, _ = block_span(event, 'trigger')
    event = event[:opening+1] + '\n        NOT = { has_variable = ros_managed } # The mod owns the inheritance crisis.\n' + event[opening+1:]
    patched = patched[:a] + event + patched[b:]
for event_id in [30, 31, 50]:
    a, _, b = block_span(patched, f'flavor_ser.{event_id}')
    event = patched[a:b]
    x, _, y = block_span(event, 'dynamic_historical_event')
    event = event[:x] + '\n    # Offered by the Rise of Serbia situation actions.\n' + event[y:]
    if event_id == 30:
        # Keep the ambition reward, but collect distinct foreign-owned provinces
        # from all objective regions. This also avoids granting CBs on ourselves.
        insert = '''
    option = {
        name = flavor_ser.30.a
        custom_tooltip = granted_claims_stefan_uros_tt
        scope:target_character = {
            add_character_modifier = { modifier = insatiable_ambition years = -1 mode = replace }
        }
'''
        for province in [p for _, ps in REGIONS.values() for p in ps]:
            insert += f'''\n        province_definition:{province} = {{
            every_ownable_location_in_province_definition = {{
                limit = {{ has_owner = yes NOT = {{ this = location:durres }} NOT = {{ ROOT = {{ owns_or_non_sovereign_subject_owns = prev }} }} }}
                province = {{ add_to_list = ros_claim_provinces }}
            }}
        }}\n'''
        insert += '''
        every_in_list = {
            variable = ros_claim_provinces
            save_scope_as = ros_claim_province
            owner = { save_scope_as = ros_claim_owner }
            ROOT = { add_casus_belli = { target = scope:ros_claim_owner type = casus_belli:cb_conquer_province province = scope:ros_claim_province years = 20 } }
        }
    }
'''
        option_start, _, option_end = block_span(event, 'option')
        event = event[:option_start] + insert + event[option_end:]
    if event_id == 31:
        x, opening, y = block_span(event, 'trigger')
        event = event[:x] + '''\n    trigger = { ros_coronation_ready = yes }\n''' + event[y:]
    if event_id == 50:
        # Triggered events still get a valid vanilla eligibility check.
        x, _, y = block_span(event, 'trigger')
        event = event[:x] + '\n    trigger = { ros_code_ready = yes }\n' + event[y:]
    if event_id in [31, 50]:
        event = event[:-1] + '\n    after = { ros_refresh_realm = yes }\n' + event[-1:]
    patched = patched[:a] + event + patched[b:]
write('in_game/events/DHE/flavor_ser.txt', patched, bom=True)
disaster = (REF / 'in_game/common/disasters/curse_of_stefan_uros_iii.txt').read_text(encoding='utf-8-sig')
_, opening, _ = block_span(disaster, 'can_start')
disaster = disaster[:opening+1] + '\n        NOT = { has_variable = ros_managed }\n' + disaster[opening+1:]
write('in_game/common/disasters/curse_of_stefan_uros_iii.txt', disaster, bom=True)

# Monthly calculations use existing game metrics; there is no second estate system.
effects = '''ros_refresh_realm = {
    set_variable = { name = ros_conquests value = 0 }
    set_variable = { name = ros_delegations value = 0 }
    set_variable = { name = ros_unsettled value = 0 }
'''
for region in REGIONS:
    effects += f'''    if = {{ limit = {{ ros_{region}_controlled = yes }}
        change_variable = {{ name = ros_conquests add = 1 }}
        if = {{ limit = {{ NOT = {{ has_variable = ros_{region}_settled }} }} change_variable = {{ name = ros_unsettled add = 1 }} }}
    }}
    if = {{ limit = {{ has_variable = ros_{region}_delegated }} change_variable = {{ name = ros_delegations add = 1 }} }}
'''
effects += '''    set_variable = { name = ros_strain value = 40 }
    change_variable = { name = ros_strain add = { value = var:ros_delegations multiply = 8 } }
    change_variable = { name = ros_strain add = { value = var:ros_unsettled multiply = 8 } }
    if = { limit = { has_policy = law_of_the_pious_emperor } change_variable = { name = ros_strain subtract = 10 } }
    if = { limit = { has_variable = ros_successor_prepared } change_variable = { name = ros_strain subtract = 15 } }
    if = { limit = { has_variable = ros_compact } change_variable = { name = ros_strain subtract = 15 } }
    if = { limit = { stability < 20 } change_variable = { name = ros_strain add = 10 } }
    if = { limit = { legitimacy < 60 } change_variable = { name = ros_strain add = 10 } }
    if = { limit = { stability >= 50 } change_variable = { name = ros_strain subtract = 5 } }
    if = { limit = { legitimacy >= 80 } change_variable = { name = ros_strain subtract = 5 } }
    if = { limit = { average_control_in_home_region < 0.5 } change_variable = { name = ros_strain add = 10 } }
    if = { limit = { at_war = yes } change_variable = { name = ros_strain add = 10 } }
    set_variable = { name = ros_strain value = { value = var:ros_strain min = 0 max = 100 } }
}

ros_clear_regional_modifiers = {
'''
for region in REGIONS:
    effects += f'    remove_country_modifier = ros_{region}_delegation\n'
effects += '}\n'
write('in_game/common/scripted_effects/ros_effects.txt', effects)

def map_script():
    return '''
    map_color = {
        if = { limit = { owner ?= c:SER } value = define:NMapColors|MAP_COLOR_HIGH }
        else_if = { limit = { OR = { ros_macedonia_location = yes ros_albania_location = yes ros_epirus_location = yes ros_thessaly_location = yes } } value = define:NMapColors|MAP_COLOR_MID }
        else = { value = define:NMapColors|DEFAULT_COLOR }
    }
    legend_key = { desc = ros_map_held color = define:NMapColors|MAP_COLOR_HIGH }
    legend_key = { desc = ros_map_objectives color = define:NMapColors|MAP_COLOR_MID }
'''

rise = '''ros_rise = {
    monthly_spawn_chance = monthly_spawn_chance_unique
    content_trigger = { tag = SER }
    can_start = {
        current_year < 1340
        country_exists = c:SER
        c:SER = { ros_founder_rules = yes NOT = { has_variable = ros_managed } }
    }
    visible = { tag = SER }
    outcome = {
        name = ros_rise_handover
        desc = ros_rise_handover_desc
        trigger = { c:SER ?= { ros_founder_rules = no } }
        immediate = { c:SER ?= { set_variable = ros_inheritance_pending } }
    }
    outcome = {
        name = ros_country_lost
        trigger = { NOT = { country_exists = c:SER } }
    }
    on_start = {
        set_variable = { name = ros_country value = c:SER }
        c:SER = {
            set_variable = ros_managed
            ros_refresh_realm = yes
            trigger_event_non_silently = ros.1
        }
    }
    on_monthly = {
        c:SER ?= {
            if = {
                limit = { has_variable = ros_prepared_heir NOT = { heir ?= var:ros_prepared_heir } ros_founder_rules = yes }
                remove_variable = ros_prepared_heir
                remove_variable = ros_successor_prepared
            }
            ros_refresh_realm = yes
        }
    }
'''+ map_script() + '}\n'
write('in_game/common/situations/ros_rise.txt', rise)

inheritance = '''ros_inheritance = {
    monthly_spawn_chance = monthly_spawn_chance_unique
    content_trigger = { tag = SER }
    can_start = { c:SER ?= { has_variable = ros_inheritance_pending NOT = { has_variable = ros_inheritance_started } } }
    visible = { tag = SER }
    outcome = {
        name = ros_central_victory
        desc = ros_central_victory_desc
        trigger = { c:SER ?= { has_variable = ros_central_victory } }
        immediate = { c:SER ?= { ros_clear_regional_modifiers = yes trigger_event_non_silently = ros.40 } }
    }
    outcome = {
        name = ros_compact_victory
        desc = ros_compact_victory_desc
        trigger = { c:SER ?= { has_variable = ros_compact_victory } }
        immediate = { c:SER ?= { trigger_event_non_silently = ros.41 } }
    }
    outcome = {
        name = ros_unsettled_end
        desc = ros_unsettled_end_desc
        trigger = { c:SER ?= { var:ros_inheritance_months >= 240 } }
        immediate = { c:SER ?= { trigger_event_non_silently = ros.42 } }
    }
    outcome = { name = ros_country_lost trigger = { NOT = { country_exists = c:SER } } }
    on_start = {
        set_variable = { name = ros_country value = c:SER }
        c:SER = {
            set_variable = ros_inheritance_started
            remove_variable = ros_inheritance_pending
            set_variable = { name = ros_inheritance_months value = 0 }
            set_variable = { name = ros_stable_months value = 0 }
            if = { limit = { has_variable = ros_prepared_heir NOT = { ruler ?= var:ros_prepared_heir } }
                remove_variable = ros_successor_prepared
            }
            ros_refresh_realm = yes
            trigger_event_non_silently = ros.2
        }
    }
    on_monthly = {
        c:SER ?= {
            change_variable = { name = ros_inheritance_months add = 1 }
            ros_refresh_realm = yes
            if = {
                limit = { var:ros_strain <= 40 at_war = no stability >= 20 legitimacy >= 60 ros_all_held_regions_settled = yes }
                change_variable = { name = ros_stable_months add = 1 }
            }
            else = { set_variable = { name = ros_stable_months value = 0 } }
        }
    }
'''+ map_script() + '}\n'
write('in_game/common/situations/ros_inheritance.txt', inheritance)

events = '''namespace = ros
ros.1 = {
    type = country_event title = ros.1.title desc = ros.1.desc
    image = "gfx/interface/illustrations/institutions/legalism.dds"
    option = { name = ros.1.a }
}
ros.2 = {
    type = country_event title = ros.2.title desc = ros.2.desc
    image = "gfx/interface/illustrations/disaster/coup_attempt.dds"
    option = {
        name = ros.2.a
        if = { limit = { NOT = { has_variable = ros_successor_prepared } } add_legitimacy = -10 add_stability = -10 }
    }
}
'''
for n, (region, (label, _)) in enumerate(REGIONS.items(), 10):
    events += f'''ros.{n} = {{
    type = country_event title = ros.{n}.title desc = ros.{n}.desc
    image = "gfx/interface/illustrations/institutions/legalism.dds"
    option = {{
        name = ros_settle_royal
        trigger = {{ is_during_bankruptcy = no }}
        ai_chance = {{ factor = 50 }}
        change_gold_effect = {{ scale = -6 }}
        add_stability = -5
        add_estate_satisfaction = {{ type = estate_type:nobles_estate value = -0.05 }}
        set_variable = ros_{region}_settled
        custom_tooltip = ros_royal_settlement_tt
        ros_refresh_realm = yes
    }}
    option = {{
        name = ros_settle_delegated
        ai_chance = {{ factor = 50 }}
        add_estate_satisfaction = {{ type = estate_type:nobles_estate value = 0.05 }}
        set_variable = ros_{region}_settled
        set_variable = ros_{region}_delegated
        add_country_modifier = {{ modifier = ros_{region}_delegation years = -1 mode = replace }}
        custom_tooltip = ros_delegated_settlement_tt
        ros_refresh_realm = yes
    }}
}}
'''
    loc(f'ros.{n}.title', f'The Settlement of {label}')
    loc(f'ros.{n}.desc', f'Our authority over {label} must take a lasting form. Royal officials demand money and political support. Entrusting the region to its magnates costs less today, but gives them privileges the next ruler will inherit.')
events += '''ros.40 = {
    type = country_event title = ros.40.title desc = ros.40.desc
    image = "gfx/interface/illustrations/institutions/legalism.dds"
    option = {
        name = ros.40.a
        add_legitimacy = 10 add_prestige = 10
        add_country_modifier = { modifier = ros_enduring_crown years = 25 mode = replace }
    }
}
ros.41 = {
    type = country_event title = ros.41.title desc = ros.41.desc
    image = "gfx/interface/illustrations/institutions/legalism.dds"
    option = {
        name = ros.41.a
        add_legitimacy = 10 add_stability = 10
        add_country_modifier = { modifier = ros_enduring_compact years = 25 mode = replace }
    }
}
ros.42 = {
    type = country_event title = ros.42.title desc = ros.42.desc
    image = "gfx/interface/illustrations/disaster/coup_attempt.dds"
    option = {
        name = ros.42.a
        add_legitimacy = -10
        add_country_modifier = { modifier = ros_unfinished_inheritance years = 15 mode = replace }
    }
}
'''
write('in_game/events/situations/ros_events.txt', events, bom=True)

actions = ''
action_ids = []
def action(key, title, desc, allow, effect, phase='both', price='ros_free', potential='', ai=10, cooldown=1):
    global actions
    action_ids.append(key)
    loc(key, title); loc(key+'_desc', desc)
    visible = 'OR = { situation:ros_rise = this situation:ros_inheritance = this }' if phase == 'both' else f'situation:ros_{phase} = this'
    actions += f'''{key} = {{
    type = situation
    icon = examine_our_fortifications
    show_message = no
    potential = {{ scope:actor = {{ tag = SER has_variable = ros_managed {potential} }} }}
    allow = {{ scope:actor = {{ {allow} }} }}
    automation_tick = never
    automation_tick_frequency = 12
    ai_tick = monthly
    ai_tick_frequency = 6
    price = price:{price}
    cooldown = {{ type = {key}_cooldown months = {cooldown} }}
    select_trigger = {{
        looking_for_a = situation
        target_flag = recipient
        name = choose_situation
        column = {{ data = name }}
        visible = {{ {visible} situation_is_active = yes }}
    }}
    effect = {{ scope:actor = {{ {effect} }} }}
    ai_will_do = {{ add = {ai} }}
}}
'''

action('ros_claim_programme', 'Proclaim the Conquest Programme', 'Hear Dušan’s existing ambitions event, expanded with Albania and Epirus. Grants twenty-year province conquest opportunities, not cores or automatic wars. Province-level peace demands can extend beyond our historical objectives.',
       'ros_founder_rules = yes at_war = no NOT = { has_variable = ros_claims_offered }',
       'trigger_event_non_silently = flavor_ser.30 set_variable = ros_claims_offered', phase='rise')
action('ros_proclaim_empire', 'Convene the Imperial Coronation', 'Open the existing imperial proclamation and patriarchate event. Requires Skopje and the Macedonian objective, not Thessaloniki or the later conquests. This is a one-time political decision.',
       'ros_coronation_ready = yes NOT = { has_variable = ros_coronation_considered }',
       'trigger_event_non_silently = flavor_ser.31 set_variable = ros_coronation_considered', phase='rise')
action('ros_promulgate_code', 'Deliberate on the Imperial Code', 'Open the existing Code event, preserving its policy, financial cost, and estate opposition. You can consider this settlement once during the two situations.',
       'ros_code_ready = yes NOT = { has_variable = ros_code_considered }', 'trigger_event_non_silently = flavor_ser.50 set_variable = ros_code_considered')
action('ros_prepare_successor', 'Secure Recognition of the Heir', 'Pay for court assemblies recognizing the current heir. Reduces succession strain by 15 while that heir remains designated, and after succession only if that person actually inherits. This is preparation, not a forced succession.',
       'at_war = no has_heir = yes NOT = { has_variable = ros_successor_prepared }',
       'set_variable = { name = ros_prepared_heir value = heir } set_variable = ros_successor_prepared ros_refresh_realm = yes', phase='rise', price='ros_assembly')
for n, (region, (label, _)) in enumerate(REGIONS.items(), 10):
    action('ros_settle_'+region, 'Settle '+label, 'Choose royal administration or a privileged regional governorship. Requires the complete regional objective, including territory held by non-sovereign subjects. Each region can be settled once.',
           f'at_war = no ros_{region}_controlled = yes NOT = {{ has_variable = ros_{region}_settled }}',
           f'trigger_event_non_silently = ros.{n}')
    action('ros_revoke_'+region, 'Reform the Governorship of '+label, 'Replace the regional delegation with royal administration. Costs six months of income and government power, angers the nobility, and removes the associated tax concession and succession burden.',
           f'at_war = no is_during_bankruptcy = no ros_{region}_controlled = yes has_variable = ros_{region}_delegated',
           f'''change_gold_effect = {{ scale = -6 }}
           add_estate_satisfaction = {{ type = estate_type:nobles_estate value = -0.1 }}
           add_stability = -5
           remove_variable = ros_{region}_delegated
           remove_country_modifier = ros_{region}_delegation
           ros_refresh_realm = yes''', price='ros_reform', phase='inheritance')
action('ros_negotiate_compact', 'Negotiate the Imperial Compact', 'Recognize regional privileges in exchange for a common political settlement. Reduces succession strain by 15 but adds a lasting tax concession. The compact and a centralized settlement are alternative paths.',
       'at_war = no var:ros_inheritance_months >= 12 NOT = { has_variable = ros_compact }',
       '''set_variable = ros_compact
       add_estate_satisfaction = { type = estate_type:nobles_estate value = 0.1 }
       add_country_modifier = { modifier = ros_compact_concessions years = -1 mode = replace }
       ros_refresh_realm = yes''', price='ros_assembly', phase='inheritance')
action('ros_confirm_crown', 'Confirm the Consolidated Empire', 'Conclude the inheritance with an imperial crown, the Code in force, no delegated governorships or compact, and 36 consecutive settled months. Requires strain at 25 or less.',
       '''country_rank = country_rank:rank_empire has_policy = law_of_the_pious_emperor
       at_war = no ros_no_delegations = yes ros_all_held_regions_settled = yes
       NOT = { has_variable = ros_compact }
       var:ros_stable_months >= 36 var:ros_strain <= 25
       NOT = { has_variable = ros_compact_victory }''',
       'set_variable = ros_central_victory', phase='inheritance', ai=100)
action('ros_confirm_compact', 'Ratify the Enduring Compact', 'Conclude the inheritance with an imperial crown, a negotiated compact, and 36 consecutive settled months. Regional concessions remain in force. Requires strain at 40 or less.',
       '''country_rank = country_rank:rank_empire at_war = no has_variable = ros_compact
       ros_all_held_regions_settled = yes var:ros_stable_months >= 36 var:ros_strain <= 40
       NOT = { has_variable = ros_central_victory }''',
       'set_variable = ros_compact_victory', phase='inheritance', ai=100)
write('in_game/common/generic_actions/ros_actions.txt', actions)
write('in_game/common/generic_action_ai_lists/ros_actions_list.txt', '''ros_actions_list = {
    potential = { tag = SER OR = { can_see_situation = situation:ros_rise can_see_situation = situation:ros_inheritance } }
    actions = { '''+' '.join(action_ids)+''' }
}''')
write('in_game/common/prices/ros_prices.txt', '''ros_free = { gold = 0 }
ros_assembly = { scaled_gold = 2 government_power = 5 }
ros_reform = { government_power = 10 }
''')

modifiers = ''
for region, (label, _) in REGIONS.items():
    modifiers += f'''ros_{region}_delegation = {{
    game_data = {{ category = country }}
    tax_income_efficiency = -0.025
}}
'''
    loc('ros_'+region+'_delegation', 'Privileges of '+label)
    loc('ros_'+region+'_delegation_desc', 'The regional governorship enjoys a fiscal concession. It adds 8 succession strain until reformed.')
modifiers += '''ros_compact_concessions = { game_data = { category = country } tax_income_efficiency = -0.05 }
ros_enduring_crown = { game_data = { category = country } monthly_legitimacy = 0.1 global_monthly_control = 0.001 }
ros_enduring_compact = { game_data = { category = country } monthly_legitimacy = 0.1 }
ros_unfinished_inheritance = { game_data = { category = country } monthly_legitimacy = -0.1 }
'''
write('main_menu/common/static_modifiers/ros_modifiers.txt', modifiers)

# Country custom localization lets the same cards work in either situation.
customloc = ''
def custom(key, entries, fallback):
    global customloc
    customloc += key + ' = {\n    type = country\n'
    for trigger, textkey, text in entries:
        customloc += f'    text = {{ trigger = {{ {trigger} }} localization_key = {textkey} }}\n'
        loc(textkey, text)
    customloc += f'    text = {{ fallback = yes localization_key = {key}_fallback }}\n}}\n'
    loc(key+'_fallback', fallback)

for region, (label, provinces) in REGIONS.items():
    custom('ros_'+region+'_status', [
        (f'ros_{region}_controlled = no has_variable = ros_{region}_settled', 'ros_'+region+'_lost', '#R Territory lost#!. Earlier political commitments remain until reformed.'),
        (f'ros_{region}_controlled = yes has_variable = ros_{region}_delegated', 'ros_'+region+'_delegated_status', '#Y Delegated governorship#!. +8 succession strain; fiscal privileges remain.'),
        (f'ros_{region}_controlled = yes has_variable = ros_{region}_settled', 'ros_'+region+'_royal_status', '#G Royal administration established#!.'),
        (f'ros_{region}_controlled = yes', 'ros_'+region+'_unsettled_status', '#Y Territorial objective achieved#!. Use Settle '+label+' below to choose an administration.')
    ], 'Territorial objective incomplete. Includes '+', '.join(p.replace('_province','').replace('_',' ').title() for p in provinces)+'.'+(' Durrës is excluded.' if region=='albania' else ''))
custom('ros_code_status', [('has_policy = law_of_the_pious_emperor','ros_code_enacted','#G The imperial Code is in force#!.'),('has_variable = ros_code_considered','ros_code_declined','#Y The Code was considered but is not in force#!.')], 'The Code awaits deliberation. Requires empire rank and Dušan, a lawgiver, or a ruler with administrative ability above 80.')
custom('ros_crown_status', [('country_rank = country_rank:rank_empire','ros_crown_achieved','#G Imperial rank achieved#!.'),('has_variable = ros_coronation_considered','ros_crown_declined','#Y The imperial proclamation was declined#!. This first version offers the ceremony once.')], 'Coronation requires peace, direct ownership of Skopje, and the Macedonian objective. Thessaloniki, Albania, Epirus, and Thessaly are not requirements.')
custom('ros_heir_status', [('has_variable = ros_successor_prepared','ros_heir_prepared','#G The designated successor was recognized#!. Succession strain reduced by 15.')], 'No recognized succession preparation. During Dušan’s reign, secure recognition of the current heir using the action below.')
custom('ros_compact_status', [('has_variable = ros_compact','ros_compact_signed','#G The compact has been negotiated#!. Strain reduced by 15; a 5% tax-efficiency concession remains.')], 'No compact has been negotiated. The successor may bargain with regional lords after the first year.')
write('in_game/common/customizable_localization/ros_customloc.txt', customloc)

def card(titlekey, body, cardkey):
    return f'''situation_card_expandable = {{
        blockoverride "header_button_onclick" {{ onclick = "[LateralView.Vars.Toggle('{cardkey}')]" }}
        blockoverride "header_text" {{ text = "{titlekey}" }}
        blockoverride "header_icon" {{ texture = "gfx/interface/icons/disasters/end_requirements_green.dds" }}
        blockoverride "bottom_content_onclick" {{ visible = "[Not(LateralView.Vars.Exists('{cardkey}'))]" }}
        blockoverride "icon_replace_visible_yes" {{ visible = "[Not(LateralView.Vars.Exists('{cardkey}'))]" }}
        blockoverride "icon_replace_visible_not" {{ visible = "[LateralView.Vars.Exists('{cardkey}')]" }}
        blockoverride "bottom_content" {{
            text_multi = {{
                margin = {{ 12 8 }}
                layoutpolicy_horizontal = expanding
                max_width = 460
                autoresize = yes
                text = "{body}"
            }}
        }}
    }}\n'''

for phase in ['rise','inheritance']:
    cards = card('ros_realm_heading', 'ros_realm_summary', 'ros_realm')
    cards += card('ros_foundations_heading', 'ros_foundations_summary', 'ros_foundations')
    for region, (label, _) in REGIONS.items():
        loc('ros_'+region+'_heading', label)
        cards += card('ros_'+region+'_heading', f"[Country.Custom('ros_{region}_status')]", 'ros_card_'+region)
    cards += card('ros_inheritance_heading', 'ros_inheritance_summary', 'ros_ledger')
    write(f'in_game/gui/panels/situation/ros_{phase}.gui', '''situation_panel = {
    blockoverride "situation_panel_image" {
        using = one_country_header_template
        blockoverride "CountryContext" {
            datacontext = "[SituationView.GetActiveSituation.GetSituation.MakeScope.GetVariable('ros_country').GetCountry]"
        }
        blockoverride "character_religion_visibility" { visible = no }
    }
    blockoverride "situation_subheader_content" {}
    blockoverride "situation_panel_main_content" {
        vbox = {
            layoutpolicy_horizontal = expanding
            spacing = 8
            datacontext = "[SituationView.GetActiveSituation.GetSituation.MakeScope.GetVariable('ros_country').GetCountry]"
            text_multi = { layoutpolicy_horizontal = expanding max_width = 470 autoresize = yes text = ros_'''+phase+'''_intro }
            '''+cards+'''
        }
    }
}''')

loc('ros_rise', 'The Rise of Serbia')
loc('ros_rise_desc', 'Conquer and settle the southern territories, proclaim the empire, and prepare a political inheritance that can survive its founder.')
loc('ros_inheritance', 'The Emperor’s Inheritance')
loc('ros_inheritance_desc', 'Resolve the obligations left by Dušan and establish a lasting imperial settlement. The crisis follows the actual succession, not a fixed historical death date.')
loc('ros_rise_intro', 'Pursue four regional objectives at your own pace. Coronation does not require all four. Each conquest needs a political settlement; delegated privileges survive the ruler who granted them. Cards can be collapsed. Figures refresh monthly and after situation actions.')
loc('ros_inheritance_intro', 'Build 36 consecutive settled months, then confirm a consolidated empire or ratify a compact using the actions below. Peace, stability of at least 20, legitimacy of at least 60, strain no higher than 40, and settlement of every fully held objective are required for that count to advance. Unresolved inheritance ends after 20 years.')
loc('ros_realm_heading', 'State of the Realm')
loc('ros_realm_summary', "Regional objectives held: [Country.MakeScope.GetVariable('ros_conquests').GetValue|0]/4\\nPrivileged governorships: [Country.MakeScope.GetVariable('ros_delegations').GetValue|0]\\nHeld objectives awaiting settlement: [Country.MakeScope.GetVariable('ros_unsettled').GetValue|0]\\nSuccession strain: [Country.MakeScope.GetVariable('ros_strain').GetValue|0]/100\\n\\nStrain starts at 40. Each delegation and each unsettled held objective adds 8. The Code subtracts 10; a recognized successor subtracts 15; the compact subtracts 15. Low stability, low legitimacy, low home-region control, and war each add 10. Lower strain is better.")
LOC['ros_realm_summary'] += '\\nStability of at least 50 and legitimacy of at least 80 each subtract 5. Strain is clamped between 0 and 100.'
loc('ros_foundations_heading', 'Foundations of Empire')
loc('ros_foundations_summary', "[Country.Custom('ros_crown_status')]\\n\\n[Country.Custom('ros_code_status')]\\n\\n[Country.Custom('ros_heir_status')]")
loc('ros_inheritance_heading', 'The Inheritance Ledger')
loc('ros_inheritance_summary', "[Country.Custom('ros_compact_status')]\\n\\nEvery privileged governorship is recorded in its regional card and country modifier. These are fiscal and political commitments, not new independent countries. The successor can reform them at a cost.\\n\\nThe centralized ending requires imperial rank, the Code, no delegations or compact, strain at most 25, and 36 consecutive settled months. The compact ending requires imperial rank, the compact, strain at most 40, and the same settled period. All four conquest objectives are optional for either ending.")
loc('ros_rise_handover', 'The Founder’s Inheritance')
loc('ros_rise_handover_desc', 'Dušan is no longer Serbia’s reigning monarch. The successor inherits the real political settlements left behind.')
loc('ros_country_lost', 'Serbia Has Fallen')
loc('ros_central_victory', 'The Consolidated Empire')
loc('ros_central_victory_desc', 'A lasting imperial settlement under royal administration. Confirm it through the situation action after meeting the conditions displayed in the inheritance ledger.')
loc('ros_compact_victory', 'The Enduring Imperial Compact')
loc('ros_compact_victory_desc', 'A lasting imperial settlement founded on negotiated privileges. Ratify it through the situation action; concessions remain in force.')
loc('ros_unsettled_end', 'An Unsettled Inheritance')
loc('ros_unsettled_end_desc', 'Twenty years have passed without a lasting imperial settlement. Existing borders remain, but unresolved authority damages legitimacy for fifteen years.')
loc('ros_map_held', 'Direct Serbian possessions')
loc('ros_map_objectives', 'Remaining regional objectives, including subject-held territory')
loc('ros_settle_royal', 'Establish royal administration.')
loc('ros_settle_delegated', 'Entrust the region to its magnates.')
loc('ros_royal_settlement_tt', 'Settle this objective without a hereditary political concession. The administrative investment is represented by its immediate financial and political costs.')
loc('ros_delegated_settlement_tt', 'Settle this objective with a privileged governorship. Adds 8 succession strain and a lasting 2.5% tax-efficiency penalty. The successor may reform it.')
loc('ros.1.title', 'An Empire in the Making')
loc('ros.1.desc', 'Dušan’s ambitions reach beyond the kingdom’s present frontiers. Conquest will require allies and administrators, and every concession made today will become part of tomorrow’s inheritance. Open The Rise of Serbia in the Situations view to pursue conquests, convene the coronation, deliberate on the Code, and prepare the succession.')
loc('ros.1.a', 'Let us build something that will endure.')
loc('ros.2.title', 'The Founder’s Shadow')
loc('ros.2.desc', 'Dušan no longer rules Serbia. The new court inherits his territories, laws, and promises to the magnates. A successor recognized before the transition begins with firmer footing; an unprepared succession costs 10 legitimacy and 10 stability. Open The Emperor’s Inheritance to negotiate a lasting settlement.')
loc('ros.2.a', 'The realm must outlive its founder.')
loc('ros.40.title', 'A Crown That Endures')
loc('ros.40.desc', 'The imperial Code and royal administration have survived the transfer of power. The empire’s authority now rests on institutions that have endured beyond their founder.')
loc('ros.40.a', 'The empire stands.')
loc('ros.41.title', 'The Imperial Compact')
loc('ros.41.desc', 'The crown and its regional lords have established a settlement that can endure beyond the founder. Their concessions remain a real cost, but negotiated authority has preserved the imperial project.')
loc('ros.41.a', 'Let every lord keep faith with the crown.')
loc('ros.42.title', 'The Unfinished Inheritance')
loc('ros.42.desc', 'Two decades have passed without a durable imperial settlement. The state survives, but contested authority continues to weaken the crown. Regional privileges remain in force; no territory is removed by this event.')
loc('ros.42.a', 'We must govern the realm we have.')
for key, name, desc in [
    ('ros_compact_concessions','Concessions of the Imperial Compact','Regional rights acknowledged in exchange for a common imperial settlement.'),
    ('ros_enduring_crown','The Enduring Imperial Crown','Royal administration and imperial law have survived the founding emperor.'),
    ('ros_enduring_compact','The Enduring Imperial Compact','The crown has secured a durable agreement with the regional lords.'),
    ('ros_unfinished_inheritance','The Unfinished Inheritance','The founding emperor’s political inheritance remains unresolved.')]:
    loc(key,name);loc(key+'_desc',desc)

# Dynamic counters shown only during the inheritance; GUI never reads unset values.
panel = MOD / 'in_game/gui/panels/situation/ros_inheritance.gui'
panel.write_text(panel.read_text().replace('text = ros_inheritance_intro', '''text = ros_inheritance_intro }
            text_multi = { layoutpolicy_horizontal = expanding max_width = 470 autoresize = yes text = ros_inheritance_counters'''), encoding='utf-8')
loc('ros_inheritance_counters', "Months since succession: [Country.MakeScope.GetVariable('ros_inheritance_months').GetValue|0]/240\\nConsecutive settled months: [Country.MakeScope.GetVariable('ros_stable_months').GetValue|0]/36")

# Override only wording made inaccurate by the revised conquest/coronation rules.
loc('granted_claims_stefan_uros_tt','Conquest opportunities cover Macedonia, Thessaly, Albania, and Epirus. Durrës and Thessaloniki are not historical objectives. Province conquest casus belli do not restrict the eventual peace treaty to historical borders.')
loc('ser_conquest_into_empire','Gain twenty-year conquest opportunities in the Macedonian and Thessalian provinces listed below.')
loc('flavor_ser.31.desc','The conquests in Macedonia have given our court the standing to proclaim an empire. An assembly at Skopje can recognize the imperial title and elevate our ecclesiastical authority. Albania, Epirus, and Thessaly remain opportunities for expansion; neither Thessaloniki nor Constantinople must fall before the imperial proclamation.')
loc('ros_free','No additional cost')
loc('ros_assembly','Court assembly')
loc('ros_reform','Administrative reform')
loc('ros_actions_list','The Rise of Serbia')
for key in action_ids:
    loc(key+'_cooldown', LOC[key])

write('main_menu/localization/english/ros_l_english.yml', 'l_english:\n'+'\n'.join(' '+key+': "'+value.replace('"','\\"')+'"' for key,value in LOC.items()), bom=True)
write('.metadata/metadata.json', json.dumps({
    'name':'The Rise of Serbia', 'id':'thearhive.rise_of_serbia', 'version':'0.1.0', 'game_id':'eu5',
    'supported_game_version':'1.4.6', 'short_description':'Historical Serbian expansion and an imperial inheritance that can endure.',
    'tags':['Gameplay','Historical'], 'relationships':[], 'game_custom_data':{'multiplayer_synchronized':True}
},indent=2))

# Store exact reference hashes, not the uploaded proprietary reference archive.
write('REFERENCE_HASHES.json', json.dumps({str(p.relative_to(REF)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [REF/'in_game/events/DHE/flavor_ser.txt',REF/'in_game/common/disasters/curse_of_stefan_uros_iii.txt']},indent=2))

# Simple geometric UI emblems; these are not historical coats of arms.
from PIL import Image, ImageDraw
icons = MOD/'main_menu/gfx/interface/icons/situations'
icons.mkdir(parents=True,exist_ok=True)
for name, background in [('ros_rise',(120,24,31,255)),('ros_inheritance',(40,52,90,255))]:
    image=Image.new('RGBA',(128,128),(0,0,0,0));draw=ImageDraw.Draw(image)
    draw.polygon([(20,12),(108,12),(108,70),(96,91),(64,116),(32,91),(20,70)],fill=background,outline=(228,190,102,255),width=5)
    draw.rectangle((58,27,70,92),fill=(241,220,165,255));draw.rectangle((38,47,90,59),fill=(241,220,165,255))
    image.save(icons/(name+'.dds'))
print('Built', len(list(MOD.rglob('*'))), 'entries;',len(LOC),'localization keys;',len(action_ids),'actions')

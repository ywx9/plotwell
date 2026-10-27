"""Constrain the two planning stages so the model cannot emit lower-level bodies."""


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def planning_schema(stage):
    string = {'type': 'string'}
    integer = {'type': 'integer', 'minimum': 1}
    empty = {'const': []}
    groups = {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}}}
    if stage == '1.outline':
        chapter = obj({'title': string, 'synopsis': string, 'turn_budget': integer, 'scenes': empty})
        story = obj({'title': string, 'turn_budget': integer,
                     'chapters': {'type': 'array', 'minItems': 1, 'items': chapter}})
        return obj({'story': story, 'chapter_events': groups})
    if stage.startswith('2.scenes.'):
        scene = obj({'synopsis': string, 'turn_budget': integer, 'time_point': string,
                     'location': string, 'turns': empty})
        return obj({'scenes': {'type': 'array', 'minItems': 1, 'items': scene}, 'scene_events': groups})
    return None


def response_schema(stage, context):
    schema = planning_schema(stage)
    if schema:
        return schema
    nullable = {'type': ['string', 'null']}
    if stage.startswith(('3.draft.', '6.edit.')):
        names = list(context['global_reference']['characters'])
        turn = obj({'speaker': {'enum': names + [None]}, 'speech': nullable,
                    'thought': nullable, 'narration': nullable})
        turns = {'type': 'array', 'minItems': 1, 'items': turn}
        visible = {'type': 'array', 'minItems': 1, 'items': obj({
            'witnesses': {'type': 'array', 'items': {'enum': [n for n in names if n != 'narrator']}},
            'observation': nullable})}
        if stage.startswith('6.edit.'):
            count = context['scene']['turn_budget']
            turns.update(minItems=count, maxItems=count)
            visible.update(minItems=count, maxItems=count)
        return obj({'turns': turns, 'continuity_summary': {'type': 'string', 'maxLength': 1000},
                    'visibility': visible, 'knowledge_updates': {'type': 'array', 'items': obj({
                        'turn': {'type': 'integer', 'minimum': 0},
                        'actor': {'enum': [n for n in names if n != 'narrator']},
                        'subject': {'enum': [n for n in names if n != 'narrator']},
                        'fact': {'type': 'string'}})}})
    if stage.startswith('7.B.'):
        return obj({'speech': nullable, 'thought': nullable})
    if stage.startswith('7.A.'):
        return obj({'performances': {'type': 'array', 'items': obj({
            'turn': {'type': 'integer', 'enum': [int(i) for i in context['turn_contexts']]},
            'speech': nullable, 'thought': nullable})}})
    return None

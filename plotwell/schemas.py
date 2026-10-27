"""Constrain the two planning stages so the model cannot emit lower-level bodies."""


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def planning_schema(stage):
    string = {'type': 'string'}
    integer = {'type': 'integer', 'minimum': 1}
    empty = {'const': []}
    groups = {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}}}
    if stage == '1.outline':
        chapter = obj({'title': string, 'synopsis': string, 'target_turns': integer, 'scenes': empty})
        story = obj({'title': string, 'synopsis': string, 'target_turns': integer,
                     'chapters': {'type': 'array', 'minItems': 1, 'items': chapter}})
        return obj({'story': story, 'chapter_events': groups})
    if stage.startswith('2.scenes.'):
        scene = obj({'synopsis': string, 'target_turns': integer, 'time_point': string,
                     'location': string, 'turns': empty})
        return obj({'scenes': {'type': 'array', 'minItems': 1, 'items': scene}, 'scene_events': groups})
    return None


def response_schema(stage, context):
    schema = planning_schema(stage)
    if schema:
        return schema
    nullable = {'type': ['string', 'null']}
    nonempty = {'type': 'string', 'minLength': 1}
    if stage.startswith('3.draft.'):
        names = list(context['global_reference']['characters'])
        turn = {'anyOf': [
            obj({'speaker': {'enum': [n for n in names if n != 'narrator']},
                 'speech': nonempty, 'thought': nonempty, 'narration': nullable}),
            obj({'speaker': {'enum': ['narrator', None]}, 'speech': {'type': 'null'},
                 'thought': {'type': 'null'}, 'narration': nonempty}),
        ]}
        turns = {'type': 'array', 'minItems': 1, 'items': turn}
        visible = {'type': 'array', 'minItems': 1, 'items': obj({
            'witnesses': {'type': 'array', 'items': {'enum': [n for n in names if n != 'narrator']}},
            'observation': nullable})}
        return obj({'turns': turns, 'continuity_summary': {'type': 'string', 'maxLength': 1000},
                    'visibility': visible, 'knowledge_updates': {'type': 'array', 'items': obj({
                        'turn': {'type': 'integer', 'minimum': 0},
                        'actor': {'enum': [n for n in names if n != 'narrator']},
                        'subject': {'enum': [n for n in names if n != 'narrator']},
                        'fact': {'type': 'string'}})}})
    if stage.startswith('4.A.'):
        return obj({'performances': {'type': 'array', 'minItems': len(context['turn_contexts']),
                                      'maxItems': len(context['turn_contexts']), 'items': obj({
            'turn': {'type': 'integer', 'enum': [int(i) for i in context['turn_contexts']]},
            'speech': nonempty, 'thought': nonempty})}})
    if stage.startswith('5.narration.'):
        count = len(context['scene']['turns'])
        return obj({'narrations': {'type': 'array', 'minItems': count, 'maxItems': count,
                                   'items': nullable},
                    'continuity_summary': {'type': 'string', 'maxLength': 1000}})
    if stage == '6.titles':
        count = len(context['chapter_synopses'])
        return obj({'title': {'type': 'string'}, 'chapter_titles': {
            'type': 'array', 'minItems': count, 'maxItems': count, 'items': {'type': 'string'}}})
    return None

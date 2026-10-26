from copy import deepcopy

from . import prompts
from .validation import keys, require, validate_final, validate_performance

FUTURE_REFERENCE_TURNS = 2


def actor_profile(plot, actor):
    character = plot['characters'][actor]
    perceptions = {}
    for subject, settings in plot['characters'].items():
        if subject == 'narrator':
            continue
        source = settings.get('how_perceived', {})
        if subject == actor:
            perceptions[subject] = source.get('self')
        else:
            perceptions[subject] = source[actor] if actor in source else source.get('others')
    return {'name': actor, 'speech_style': character.get('speech_style'),
            'thought_style': character.get('thought_style'), 'initial_perceptions': perceptions}


class Performer:
    def __init__(self, ask, retries, log):
        self.ask, self.retries, self.log = ask, retries, log

    @staticmethod
    def empty_state(plot):
        return {name: {'acquired_facts': [], 'witnessed_dialogue': [], 'past_thoughts': []}
                for name in plot['characters'] if name != 'narrator'}

    @staticmethod
    def context(plot, actor, state, scene, original, info, future):
        knowledge = deepcopy(state[actor])
        # Private memory is separately marked; it cannot become another actor's knowledge.
        thoughts = knowledge.pop('past_thoughts')
        knowledge['current_observation'] = info['observation'] if actor in info['witnesses'] else None
        return {'actor': actor_profile(plot, actor), 'character_knowledge': knowledge,
                'past_thoughts': thoughts, 'time_point': scene['time_point'], 'location': scene['location'],
                'reference_material': {'current_script': deepcopy(original),
                                       'future_script': [{'speaker': t['speaker'], 'speech': t['speech'],
                                                          'narration': t['narration']}
                                                         for t in future[:FUTURE_REFERENCE_TURNS]]}}

    @staticmethod
    def advance(state, original, performed, info):
        for name in info['witnesses']:
            if performed['speech']:
                state[name]['witnessed_dialogue'].append({'speaker': performed['speaker'], 'speech': performed['speech']})
                state[name]['witnessed_dialogue'] = state[name]['witnessed_dialogue'][-12:]
        actor = original['speaker']
        if actor in state and performed['thought']:
            state[actor]['past_thoughts'].append(performed['thought'])
            state[actor]['past_thoughts'] = state[actor]['past_thoughts'][-8:]
        for fact in info['learned']:
            item = {'subject': fact['subject'], 'fact': fact['fact']}
            if item not in state[fact['actor']]['acquired_facts']:
                state[fact['actor']]['acquired_facts'].append(item)

    def generate_performance(self, stage, context, originals, mode):
        indexes = list(originals)
        if mode == 'B':
            def validate(value):
                validate_performance(value)
                require(bool(value['speech']) == bool(originals[indexes[0]]['speech']), 'Keep speech presence')
            instruction = prompts.ACT + '\nB方式。現在の1ターンのみ。出力は {"speech":文字列またはnull,"thought":文字列またはnull}。'
        else:
            def validate(value):
                keys(value, 'performances')
                require(isinstance(value['performances'], list), 'performances must be a list')
                require([v.get('turn') for v in value['performances']] == indexes, 'Keep assigned turn indexes and order')
                for item in value['performances']:
                    keys(item, 'turn speech thought')
                    validate_performance({k: item[k] for k in ('speech', 'thought')})
                    require(bool(item['speech']) == bool(originals[item['turn']]['speech']), 'Keep speech presence')
            instruction = prompts.ACT + '\nA方式。自分の担当ターンを一括で演じる。出力は {"performances":[{"turn":担当番号,"speech":文字列またはnull,"thought":文字列またはnull},...]}。'
        result = self.ask(stage, instruction, context, validate)
        return ({indexes[0]: result} if mode == 'B' else
                {v['turn']: {k: v[k] for k in ('speech', 'thought')} for v in result['performances']})

    def run(self, checkpoint, mode='B'):
        require(mode in ('A', 'B'), 'Unknown acting mode')
        original, plot = checkpoint['story'], checkpoint['plot']
        result = deepcopy(original)
        state = self.empty_state(plot)
        for ci, chapter in enumerate(original['chapters']):
            for si, scene in enumerate(chapter['scenes']):
                metadata = checkpoint['directing'][ci][si]
                if mode == 'A':
                    # Build every context from the SAME narrator baseline, before any actor output exists.
                    baseline = deepcopy(state)
                    contexts = {}
                    for ti, (turn, info) in enumerate(zip(scene['turns'], metadata)):
                        if turn['speaker'] in baseline:
                            contexts[ti] = self.context(plot, turn['speaker'], baseline, scene, turn, info, scene['turns'][ti + 1:])
                            contexts[ti]['reference_material'].pop('future_script')
                        self.advance(baseline, turn, turn, info)
                    changes = {}
                    actors = list(dict.fromkeys(t['speaker'] for t in scene['turns'] if t['speaker'] in state))
                    for actor in actors:
                        ids = [i for i, t in enumerate(scene['turns']) if t['speaker'] == actor]
                        context = {'actor': actor_profile(plot, actor),
                                   'reference_script': [{k: t[k] for k in ('speaker', 'speech', 'narration')}
                                                        for t in scene['turns']],
                                   'turn_contexts': {str(i): contexts[i] for i in ids}}
                        changes.update(self.generate_performance(f'7.A.{ci + 1}.{si + 1}.{actor}', context,
                                                                 {i: scene['turns'][i] for i in ids}, 'A'))
                else:
                    changes = {}
                for ti, (turn, info) in enumerate(zip(scene['turns'], metadata)):
                    destination = result['chapters'][ci]['scenes'][si]['turns'][ti]
                    if turn['speaker'] in state:
                        if mode == 'B':
                            context = self.context(plot, turn['speaker'], state, scene, turn, info, scene['turns'][ti + 1:])
                            changes.update(self.generate_performance(f'7.B.{ci + 1}.{si + 1}.{ti + 1}', context,
                                                                     {ti: turn}, 'B'))
                        destination.update(changes[ti])
                        self.log.event('performance_diff', mode=mode, chapter=ci + 1, scene=si + 1,
                                       turn=ti + 1, before=deepcopy(turn), after=deepcopy(destination))
                    self.advance(state, turn, turn if mode == 'A' else destination, info)
        validate_final(result, plot)
        return result

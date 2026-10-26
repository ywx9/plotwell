from __future__ import annotations

from copy import deepcopy

from . import prompts
from .performance import Performer, actor_profile
from .checkpoint import make_checkpoint, validate_checkpoint
from .validation import (keys, require, text, validate_assignment, validate_budgets,
                         validate_final, validate_outline,
                         validate_scene_result, validate_scenes)


def writer_plot(plot):
    result = deepcopy(plot)
    for name, character in result['characters'].items():
        if name != 'narrator':
            character.pop('speech_style', None)
            character.pop('thought_style', None)
    return result


class Pipeline:
    def __init__(self, llm, log):
        self.llm, self.log = llm, log

    def ask(self, stage, instructions, context, validator):
        self.log.event('stage_start', stage=stage)
        print(stage, flush=True)
        return self.llm.generate(stage, instructions, context, validator)

    def run(self, plot, mode='B', stop_after_narrator=False):
        checkpoint = self.narrate(plot)
        if stop_after_narrator:
            return deepcopy(checkpoint['story'])
        return self.replay(checkpoint, mode)

    def narrate(self, plot):
        writing = writer_plot(plot)
        catalog = [{'id': i, 'event': event} for i, event in enumerate(plot.get('milestones', []))]
        writing.pop('milestones', None)

        def check_outline(value):
            keys(value, 'story chapter_events')
            validate_outline(value['story'], plot)
            validate_assignment(value['chapter_events'], list(range(len(catalog))), len(value['story']['chapters']))

        result = self.ask('1.outline', prompts.OUTLINE, {'plot': writing, 'event_catalog': catalog}, check_outline)
        story, chapter_events = result['story'], result['chapter_events']
        scene_events = []
        for ci, chapter in enumerate(story['chapters']):
            assigned = chapter_events[ci]

            def check_scenes(value):
                keys(value, 'scenes scene_events')
                validate_scenes({'scenes': value['scenes']}, chapter['turn_budget'])
                validate_assignment(value['scene_events'], assigned, len(value['scenes']))

            result = self.ask(f'2.scenes.{ci + 1}', prompts.SCENES,
                              {'global_reference': writing, 'chapter': deepcopy(chapter),
                               'assigned_events': [catalog[i] for i in assigned],
                               'other_chapter_synopses': [c['synopsis'] for i, c in enumerate(story['chapters']) if i != ci]}, check_scenes)
            chapter['scenes'] = result['scenes']
            scene_events.append(result['scene_events'])
        self.log.event('event_assignments', chapter_events=chapter_events, scene_events=scene_events, event_catalog=catalog)

        order = [(ci, si, s) for ci, c in enumerate(story['chapters']) for si, s in enumerate(c['scenes'])]
        completed, continuity, scopes = [], '', {}
        for ci, si, scene in order:
            assigned = scene_events[ci][si]
            future = [i for i in range(len(catalog)) if i not in completed and i not in assigned]
            scope = {'assigned_events': [catalog[i] for i in assigned],
                     'completed_events': [catalog[i] for i in completed],
                     'future_events': [catalog[i] for i in future]}
            scopes[(ci, si)] = scope
            context = {'global_reference': writing, 'scene': deepcopy(scene), 'scope': scope,
                       'continuity_summary': continuity}
            result = self.write_scene(f'3.draft.{ci + 1}.{si + 1}', prompts.DRAFT, context, plot)
            scene['turns'], continuity = result['turns'], result['continuity_summary']
            completed.extend(assigned)
        self.log.event('narrator_draft', story=deepcopy(story))

        def stats(scene, ids):
            return {'synopsis': scene['synopsis'], 'old_budget': scene['turn_budget'],
                    'actual_turns': len(scene['turns']), 'minimum': 1, 'events': [catalog[i] for i in ids]}

        chapters, items = story['chapters'], []
        for ci, chapter in enumerate(chapters):
            scenes = [stats(s, scene_events[ci][si]) for si, s in enumerate(chapter['scenes'])]
            items.append({'synopsis': chapter['synopsis'], 'old_budget': chapter['turn_budget'],
                          'actual_turns': sum(s['actual_turns'] for s in scenes),
                          'minimum': len(scenes), 'events': [catalog[i] for i in chapter_events[ci]], 'scenes': scenes})
        budgets = self.allocate('4.chapter_budgets', story['turn_budget'], items)
        for chapter, budget in zip(chapters, budgets):
            chapter['turn_budget'] = budget
        for ci, chapter in enumerate(chapters):
            items = [stats(s, scene_events[ci][si]) for si, s in enumerate(chapter['scenes'])]
            budgets = self.allocate(f'5.scene_budgets.{ci + 1}', chapter['turn_budget'], items)
            for scene, budget in zip(chapter['scenes'], budgets):
                scene['turn_budget'] = budget

        continuity = ''
        directing = [[None for _ in c['scenes']] for c in chapters]
        for index, (ci, si, scene) in enumerate(order):
            context = {'global_reference': writing, 'scene': deepcopy(scene), 'scope': scopes[(ci, si)],
                       'continuity_summary': continuity,
                       'next_scene_synopsis': order[index + 1][2]['synopsis'] if index + 1 < len(order) else None}
            result = self.write_scene(f'6.edit.{ci + 1}.{si + 1}', prompts.EDIT, context, plot,
                                      count=scene['turn_budget'])
            scene['turns'], continuity = result['turns'], result['continuity_summary']
            directing[ci][si] = result['visibility']
            # Sparse knowledge updates are produced with the script, not by another model pass.
            for ti, info in enumerate(directing[ci][si]):
                info['learned'] = [{k: value for k, value in v.items() if k != 'turn'}
                                   for v in result['knowledge_updates'] if v['turn'] == ti]
        validate_final(story, plot)
        self.log.event('narrator_final', story=deepcopy(story))
        checkpoint = make_checkpoint(plot, story, directing, chapter_events, scene_events)
        self.log.event('narrator_checkpoint', checkpoint=checkpoint)
        return checkpoint

    def allocate(self, stage, total, items):
        def validate(value):
            keys(value, 'budgets reasons')
            validate_budgets({'budgets': value['budgets']}, total, [i['minimum'] for i in items])
            require(isinstance(value['reasons'], list) and len(value['reasons']) == len(items), 'Explain each editorial allocation')
            for reason in value['reasons']:
                text(reason, 'allocation reason')

        result = self.ask(stage, prompts.ALLOCATE, {'total': total, 'items': items}, validate)
        self.log.event('budget_decision', stage=stage, before=[i['old_budget'] for i in items],
                       observed=[i['actual_turns'] for i in items], **result)
        return result['budgets']

    def write_scene(self, stage, prompt, context, plot, count=None):
        return self.ask(stage, prompt, context,
                        lambda v: validate_scene_result(v, plot['characters'], count))

    def replay(self, checkpoint, mode='B'):
        validate_checkpoint(checkpoint)
        self.log.event('narrator_checkpoint', checkpoint=deepcopy(checkpoint))
        self.log.event('performance_source', mode=mode, narrator_hash=checkpoint['narrator_hash'])
        story = Performer(self.ask, self.llm.retries, self.log).run(checkpoint, mode)
        validate_checkpoint(checkpoint)
        return story

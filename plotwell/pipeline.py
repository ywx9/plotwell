from __future__ import annotations

from copy import deepcopy

from . import prompts
from .performance import Performer
from .checkpoint import make_checkpoint, validate_checkpoint
from .validation import (keys, require, text, validate_assignment,
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

    def run(self, plot, mode='A', stop_after_narrator=False):
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
                validate_scenes({'scenes': value['scenes']}, chapter['target_turns'])
                validate_assignment(value['scene_events'], assigned, len(value['scenes']))

            result = self.ask(f'2.scenes.{ci + 1}', prompts.SCENES,
                              {'global_reference': writing, 'story_synopsis': story['synopsis'], 'chapter': deepcopy(chapter),
                               'assigned_events': [catalog[i] for i in assigned],
                               'other_chapter_synopses': [c['synopsis'] for i, c in enumerate(story['chapters']) if i != ci]}, check_scenes)
            chapter['scenes'] = result['scenes']
            scene_events.append(result['scene_events'])
        self.log.event('event_assignments', chapter_events=chapter_events, scene_events=scene_events, event_catalog=catalog)

        order = [(ci, si, s) for ci, c in enumerate(story['chapters']) for si, s in enumerate(c['scenes'])]
        completed, continuity = [], ''
        directing = [[None for _ in c['scenes']] for c in story['chapters']]
        for ci, si, scene in order:
            assigned = scene_events[ci][si]
            future = [i for i in range(len(catalog)) if i not in completed and i not in assigned]
            scope = {'assigned_events': [catalog[i] for i in assigned],
                     'completed_events': [catalog[i] for i in completed],
                     'future_events': [catalog[i] for i in future]}
            context = {'global_reference': writing, 'scene': deepcopy(scene), 'scope': scope,
                       'continuity_summary': continuity, 'story_synopsis': story['synopsis'],
                       'chapter_synopsis': story['chapters'][ci]['synopsis']}
            result = self.write_scene(f'3.draft.{ci + 1}.{si + 1}', prompts.DRAFT, context, plot)
            scene['turns'], continuity = result['turns'], result['continuity_summary']
            directing[ci][si] = [
                {**info, 'learned': [{k: v for k, v in fact.items() if k != 'turn'}
                                     for fact in result['knowledge_updates'] if fact['turn'] == ti]}
                for ti, info in enumerate(result['visibility'])]
            completed.extend(assigned)
        self.log.event('narrator_draft', story=deepcopy(story))

        validate_final(story, plot)
        self.log.event('narrator_final', story=deepcopy(story))
        checkpoint = make_checkpoint(plot, story, directing, chapter_events, scene_events)
        self.log.event('narrator_checkpoint', checkpoint=checkpoint)
        return checkpoint

    def write_scene(self, stage, prompt, context, plot, count=None):
        return self.ask(stage, prompt, context,
                        lambda v: validate_scene_result(v, plot['characters'], count))

    def replay(self, checkpoint, mode='A'):
        validate_checkpoint(checkpoint)
        self.log.event('narrator_checkpoint', checkpoint=deepcopy(checkpoint))
        self.log.event('performance_source', mode=mode, narrator_hash=checkpoint['narrator_hash'])
        story = Performer(self.ask, self.llm.retries, self.log).run(checkpoint, mode)
        validate_checkpoint(checkpoint)
        return self.finish(story, checkpoint['plot'])

    def finish(self, story, plot):
        continuity = ''
        language = plot.get('language', 'Japanese')
        for ci, chapter in enumerate(story['chapters']):
            for si, scene in enumerate(chapter['scenes']):
                def validate(value):
                    keys(value, 'narrations continuity_summary')
                    require(isinstance(value['narrations'], list) and
                            len(value['narrations']) == len(scene['turns']), 'Keep every turn')
                    for narration, turn in zip(value['narrations'], scene['turns']):
                        text(narration, 'narration', nullable=True)
                        if not turn['speech'] and not turn['thought']:
                            text(narration, 'narration-only turn')
                    text(value['continuity_summary'], 'continuity_summary')
                    require(len(value['continuity_summary']) <= 1000, 'Summary too long')
                value = self.ask(f'5.narration.{ci + 1}.{si + 1}', prompts.NARRATION,
                                 {'language': language, 'scene': deepcopy(scene),
                                  'narrator': plot['characters']['narrator'],
                                  'continuity_summary': continuity}, validate)
                for turn, narration in zip(scene['turns'], value['narrations']):
                    turn['narration'] = narration
                continuity = value['continuity_summary']

        def validate_titles(value):
            keys(value, 'title chapter_titles')
            text(value['title'], 'title')
            require(isinstance(value['chapter_titles'], list) and
                    len(value['chapter_titles']) == len(story['chapters']), 'Keep chapter count')
            for title in value['chapter_titles']:
                text(title, 'chapter title')

        titles = self.ask('6.titles', prompts.TITLES,
                          {'language': language, 'requested_title': plot.get('title'),
                           'story_synopsis': story['synopsis'],
                           'chapter_synopses': [c['synopsis'] for c in story['chapters']]}, validate_titles)
        story['title'] = titles['title']
        for chapter, title in zip(story['chapters'], titles['chapter_titles']):
            chapter['title'] = title
        validate_final(story, plot)
        return story

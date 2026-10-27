from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from plotwell import prompts
from plotwell.__main__ import main
from plotwell.checkpoint import digest, read_checkpoint, validate_checkpoint
from plotwell.performance import actor_profile
from plotwell.pipeline import Pipeline, writer_plot
from plotwell.runtime import LLM, RunLog
from plotwell.log_format import LogFormat, records
from plotwell.schemas import planning_schema, response_schema
from plotwell.validation import (validate_assignment, validate_budgets, validate_final,
                                 validate_outline, validate_performance, validate_plot, validate_scenes, validate_turns)


PLOT = {'title': 'テスト', 'premise': '二人の会話', 'target_turns': 12,
        'milestones': ['導入', '発見', '決定的な告白', '決着'], 'characters': {
    'narrator': {'speech_style': '簡潔'},
    'A': {'speech_style': '敬語', 'thought_style': '率直', 'background': 'A_SECRET',
          'how_perceived': {'self': 'A_SELF', 'others': 'A_PUBLIC', 'B': 'A_FROM_B'}},
    'B': {'speech_style': '常体', 'background': 'B_SECRET',
          'how_perceived': {'self': 'B_SELF', 'others': 'B_PUBLIC', 'A': 'B_FROM_A'}},
}}


def turn(speaker='A', speech='手紙を挟んでいた。'):
    return {'speaker': speaker, 'speech': speech, 'thought': speaker + '_PRIVATE', 'narration': '二人が話す。'}


def outline():
    return {'synopsis': 'Complete story synopsis', 'title': 'テスト', 'target_turns': 12,
            'chapters': [{'title': f'章{i}', 'synopsis': f'章{i}概要', 'target_turns': 6, 'scenes': []} for i in (1, 2)]}


class FakeLLM:
    retries = 1
    timeout = 300
    max_tokens = 8192

    def __init__(self):
        self.calls = []

    def generate(self, stage, instruction, context, validate):
        self.calls.append((stage, instruction, deepcopy(context)))
        if stage == '1.outline':
            value = {'story': outline(), 'chapter_events': [[0, 1], [2, 3]]}
        elif stage.startswith('2.'):
            ids = [v['id'] for v in context['assigned_events']]
            value = {'scenes': [{'synopsis': f'シーン{eid}', 'target_turns': 3,
                                  'time_point': f'時刻{eid}', 'location': f'場所{eid}', 'turns': []} for eid in ids],
                     'scene_events': [[eid] for eid in ids]}
        elif stage.startswith('3.'):
            eid = context['scope']['assigned_events'][0]['id']
            count = [8, 2, 2, 5][eid]
            turns = [turn('A' if i % 2 == 0 else 'B') for i in range(count)]
            turns[-1]['speech'] += f' [E{eid}]'
            value = {'turns': turns, 'continuity_summary': f'出来事{eid}まで完了した。',
                     'visibility': [{'witnesses': ['A'] if i == 0 else ['A', 'B'],
                                     'observation': '目の前で相手が頷く。'} for i in range(count)],
                     'knowledge_updates': ([{'turn': 1, 'actor': 'A', 'subject': 'B',
                                             'fact': 'Bは鍵の場所を知っていた。'}] if eid == 1 else [])}
        elif stage.startswith('4.A.'):
            value = {'performances': [
                {'turn': int(i), 'speech': 'ええ、' + c['reference_material']['current_script']['speech'],
                 'thought': '演技Aの内面'} for i, c in context['turn_contexts'].items()]}
        elif stage.startswith('5.narration.'):
            value = {'narrations': ['Localized narration' for _ in context['scene']['turns']],
                     'continuity_summary': 'Current scene completed.'}
        elif stage == '6.titles':
            value = {'title': 'Final story title',
                     'chapter_titles': ['Final chapter ' + str(i) for i in range(len(context['chapter_synopses']))]}
        else:
            raise AssertionError(stage)
        validate(value)
        return value


class GenerationTests(unittest.TestCase):
    def test_log_references_and_request_preservation(self):
        setting = 'A long character background that should only appear once in the log.'
        plot = {'characters': {'A': {'background': setting}}}
        payload = {'messages': [{'role': 'user', 'content': json.dumps(plot)}]}
        original = deepcopy(payload)
        with tempfile.TemporaryDirectory() as directory:
            log = RunLog(Path(directory), json.dumps(plot))
            try:
                log.event('llm_request', payload=payload)
                log.event('http_response', body=json.dumps({'choices': [
                    {'message': {'content': json.dumps({'speech': '{"keep": "as text"}'})}}]}))
            finally:
                log.close()
            content = log.path.read_text(encoding='utf-8')
        self.assertEqual(payload, original)
        self.assertEqual(content.count(setting), 1)
        entries = list(records(content))
        self.assertEqual(len(content.splitlines()), 3)
        reference = json.loads(entries[1]['payload']['messages'][0]['content'])['characters']['A']['background']
        self.assertEqual(reference, {'$input': ['characters', 'A', 'background']})
        self.assertEqual(LogFormat(plot).expand(reference), setting)
        response = json.loads(entries[2]['body'])
        self.assertEqual(json.loads(response['choices'][0]['message']['content'])['speech'],
                         '{"keep": "as text"}')

    def test_asymmetric_perception_direction_and_fallback(self):
        profile = actor_profile(PLOT, 'A')
        self.assertEqual(profile['initial_perceptions'], {'A': 'A_SELF', 'B': 'B_FROM_A'})
        self.assertEqual(actor_profile(PLOT, 'B')['initial_perceptions'], {'A': 'A_FROM_B', 'B': 'B_SELF'})
        for forbidden in ('SECRET', 'B_SELF', 'B_PUBLIC', 'A_PUBLIC', 'A_FROM_B'):
            self.assertNotIn(forbidden, json.dumps(profile))
        plot = deepcopy(PLOT)
        del plot['characters']['B']['how_perceived']['A']
        self.assertEqual(actor_profile(plot, 'A')['initial_perceptions']['B'], 'B_PUBLIC')
        plot['characters']['B']['how_perceived']['A'] = None
        self.assertIsNone(actor_profile(plot, 'A')['initial_perceptions']['B'])

    def test_planning_rejects_lower_level_body_and_invalid_assignments(self):
        story = outline()
        story['chapters'][0]['scenes'] = [{'turns': [turn()]}]
        with self.assertRaises(ValueError):
            validate_outline(story, PLOT)
        with self.assertRaises(ValueError):
            validate_scenes({'scenes': [{'synopsis': '概要', 'target_turns': 1, 'time_point': '今',
                                        'location': 'ここ', 'turns': [turn()]}]}, 1)
        for bad in ([[0], [0, 1]], [[1], [0]], [[0], []]):
            with self.assertRaises(ValueError):
                validate_assignment(bad, [0, 1], 2)
        self.assertNotIn('turnは', prompts.OUTLINE + prompts.SCENES)
        self.assertNotIn('milestones', prompts.ACT)
        schema = planning_schema('1.outline')
        self.assertEqual(set(schema['properties']['story']['properties']), {'title', 'synopsis', 'target_turns', 'chapters'})
        for field in ('opening', 'ending'):
            with self.assertRaises(ValueError):
                validate_outline({**outline(), field: '旧フィールド'}, PLOT)
        self.assertEqual(schema['properties']['story']['properties']['chapters']['items']['properties']['scenes'], {'const': []})
        schema = planning_schema('2.scenes.1')
        self.assertEqual(schema['properties']['scenes']['items']['properties']['turns'], {'const': []})
        schema = response_schema('3.draft.1.1', {'global_reference': PLOT, 'scene': {'target_turns': 3}})
        self.assertEqual(schema['properties']['turns']['minItems'], 1)
        self.assertNotIn('maxItems', schema['properties']['turns'])
        self.assertEqual(set(schema['properties']['turns']['items']['anyOf'][0]['required']),
                         {'speaker', 'speech', 'thought', 'narration'})

    def test_character_turn_requires_speech_and_thought(self):
        for field in ('speech', 'thought'):
            for invalid in (None, '', ' \t\n', 123):
                with self.subTest(field=field, invalid=invalid):
                    value = {**turn(), field: invalid}
                    with self.assertRaises(ValueError):
                        validate_turns({'turns': [value]}, PLOT['characters'])
                    with self.assertRaises(ValueError):
                        validate_performance({k: value[k] for k in ('speech', 'thought')})
            value = turn()
            del value[field]
            with self.assertRaises(ValueError):
                validate_turns({'turns': [value]}, PLOT['characters'])
            with self.assertRaises(ValueError):
                validate_performance({k: v for k, v in value.items() if k in ('speech', 'thought')})
        for sound in ('はぁ……', 'ふふっ', 'ちゅっ'):
            validate_turns({'turns': [turn(speech=sound)]}, PLOT['characters'])
        for speaker in (None, 'narrator'):
            validate_turns({'turns': [{'speaker': speaker, 'speech': None, 'thought': None,
                                      'narration': 'The room is quiet.'}]}, PLOT['characters'])

    def test_character_response_schemas_require_both_fields(self):
        schema = response_schema('3.draft.1.1', {'global_reference': PLOT})
        character, narration = schema['properties']['turns']['items']['anyOf']
        self.assertEqual(character['properties']['speaker']['enum'], ['A', 'B'])
        self.assertEqual(narration['properties']['speaker']['enum'], ['narrator', None])
        acting = response_schema('4.A.1.1.A', {'turn_contexts': {'0': {}}})
        performance = acting['properties']['performances']['items']
        for field in ('speech', 'thought'):
            self.assertEqual(character['properties'][field], {'type': 'string', 'minLength': 1})
            self.assertEqual(performance['properties'][field], {'type': 'string', 'minLength': 1})
            self.assertEqual(narration['properties'][field], {'type': 'null'})

    def test_six_stage_pipeline_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            log = RunLog(Path(directory), json.dumps(PLOT))
            llm = FakeLLM()
            pipeline = Pipeline(llm, log)
            try:
                checkpoint = pipeline.narrate(PLOT)
                baseline = deepcopy(checkpoint)
                self.assertEqual(len(llm.calls), 7)  # outline, two chapters, four scenes
                result = pipeline.replay(checkpoint)
                validate_final(result, PLOT)
                self.assertEqual(checkpoint, baseline)
                self.assertEqual(checkpoint['version'], 4)
                self.assertEqual([len(s['turns']) for c in result['chapters'] for s in c['scenes']], [8, 2, 2, 5])
                self.assertEqual([c['target_turns'] for c in result['chapters']], [6, 6])
                self.assertEqual(result['target_turns'], 12)
                self.assertEqual(result['title'], 'Final story title')
                self.assertEqual(result['synopsis'], 'Complete story synopsis')
                self.assertEqual(len(llm.calls), 20)  # seven planning/script, eight actors, four narration, one titles
                stages = [stage for stage, _, _ in llm.calls]
                self.assertEqual(stages[-1], '6.titles')
                self.assertTrue(all(stage.startswith('4.A.') for stage in stages[7:15]))
                for stage, instruction, context in llm.calls:
                    self.assertTrue(instruction.isascii())
                    if stage.startswith(('2.', '3.')):
                        self.assertEqual(context['story_synopsis'], result['synopsis'])
                    if stage.startswith('3.'):
                        self.assertNotIn('speech_style', context['global_reference']['characters']['A'])
                    if stage.startswith('4.'):
                        self.assertEqual(context['language'], 'Japanese')
                        self.assertNotIn('SECRET', json.dumps(context))
                        for item in context['turn_contexts'].values():
                            self.assertNotIn('future_script', item['reference_material'])
                        private = 'B_PRIVATE' if context['actor']['name'] == 'A' else 'A_PRIVATE'
                        self.assertNotIn(private, json.dumps(context))
                    if stage.startswith('5.'):
                        self.assertEqual(context['language'], 'Japanese')
                        for t in context['scene']['turns']:
                            self.assertNotIn('_PRIVATE', t['thought'])
                    if stage == '6.titles':
                        self.assertEqual(set(context), {'language', 'requested_title', 'story_synopsis', 'chapter_synopses'})
                for ci, chapter in enumerate(result['chapters']):
                    for si, scene in enumerate(chapter['scenes']):
                        for ti, t in enumerate(scene['turns']):
                            source = baseline['story']['chapters'][ci]['scenes'][si]['turns'][ti]
                            self.assertEqual(t['speaker'], source['speaker'])
                            self.assertEqual(t['narration'], 'Localized narration')
                            self.assertTrue(t['speech'].endswith(source['speech']))
            finally:
                log.close()
            self.assertEqual(read_checkpoint(log.path), checkpoint)
            replay_llm = FakeLLM()
            replay_llm.discover = lambda: None
            with patch('plotwell.__main__.LLM', return_value=replay_llm), redirect_stdout(io.StringIO()):
                status = main(['--from-narrator', str(log.path), '--output-dir', str(Path(directory) / 'out'),
                               '--env', str(Path(directory) / 'absent.env')])
            self.assertEqual(status, 0)
            self.assertEqual(len(replay_llm.calls), 13)
            self.assertTrue(all(stage.startswith(('4.', '5.', '6.')) for stage, _, _ in replay_llm.calls))
            output_log = next((Path(directory) / 'out').glob('*.log'))
            completion = next(e for e in records(output_log.read_text(encoding='utf-8')) if e.get('event') == 'run_complete')
            self.assertEqual(completion['total_turns'], 17)
            self.assertEqual(completion['target_turns'], 12)

    def test_legacy_migration_and_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            log = RunLog(Path(directory), json.dumps(PLOT))
            try:
                checkpoint = Pipeline(FakeLLM(), log).narrate(PLOT)
            finally:
                log.close()
            for version in (2, 3):
                legacy = deepcopy(checkpoint)
                legacy['version'] = version
                legacy['story'].pop('synopsis')
                for item in [legacy['plot'], legacy['story']] + legacy['story']['chapters'] + [
                        s for c in legacy['story']['chapters'] for s in c['scenes']]:
                    if 'target_turns' in item:
                        item['turn_budget'] = item.pop('target_turns')
                if version == 2:
                    legacy['story'].update(opening='old opening', ending='old ending')
                legacy['narrator_hash'] = digest(legacy['story'])
                legacy['checksum'] = digest({k: v for k, v in legacy.items() if k != 'checksum'})
                path = Path(directory) / 'legacy.log'
                def save():
                    path.write_text(json.dumps(legacy['plot']) + '\n' + json.dumps({
                        'event': 'narrator_checkpoint', 'checkpoint': legacy}), encoding='utf-8')
                save()
                migrated = read_checkpoint(path)
                self.assertEqual(migrated['version'], 4)
                self.assertEqual(migrated['story']['synopsis'], '\n'.join(c['synopsis'] for c in checkpoint['story']['chapters']))
                self.assertEqual(migrated['story']['chapters'], checkpoint['story']['chapters'])
                legacy['story']['title'] = 'tampered'
                save()
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    read_checkpoint(path)

    def test_language_validation_and_stop_after_script(self):
        for language in ('Japanese', 'ja', 'French'):
            validate_plot({**PLOT, 'language': language})
        for language in (None, '', 42):
            with self.assertRaises(ValueError):
                validate_plot({**PLOT, 'language': language})
        with tempfile.TemporaryDirectory() as directory:
            log = RunLog(Path(directory), '{}')
            llm = FakeLLM()
            try:
                result = Pipeline(llm, log).run(PLOT, stop_after_narrator=True)
            finally:
                log.close()
            self.assertEqual(len(llm.calls), 7)
            validate_final(result, PLOT)

    def test_narration_only_turn_and_explicit_language(self):
        class NarrationLLM(FakeLLM):
            def generate(self, stage, instruction, context, validate):
                def check(value):
                    if stage.startswith('3.'):
                        value['turns'][0] = {'speaker': None, 'speech': None, 'thought': None,
                                             'narration': 'They enter the room.'}
                    validate(value)
                return super().generate(stage, instruction, context, check)
        with tempfile.TemporaryDirectory() as directory:
            plot = {**PLOT, 'language': 'French'}
            log = RunLog(Path(directory), json.dumps(plot))
            llm = NarrationLLM()
            try:
                story = Pipeline(llm, log).run(plot)
            finally:
                log.close()
            for chapter in story['chapters']:
                for scene in chapter['scenes']:
                    self.assertEqual(scene['turns'][0], {'speaker': None, 'speech': None, 'thought': None,
                                                        'narration': 'Localized narration'})
            for stage, _, context in llm.calls:
                if stage.startswith(('4.', '5.', '6.')):
                    self.assertEqual(context['language'], 'French')
            actor_calls = [c for c in llm.calls if c[0].startswith('4.')]
            for _, _, context in actor_calls:
                self.assertNotIn('0', context['turn_contexts'])

    def test_finishing_rejects_missing_turns_titles_and_empty_narration(self):
        for broken in ('missing_narration', 'empty_narration', 'missing_title'):
            class BrokenLLM(FakeLLM):
                def generate(self, stage, instruction, context, validate):
                    def check(value):
                        if stage.startswith('3.'):
                            value['turns'][0] = {'speaker': None, 'speech': None, 'thought': None,
                                                 'narration': 'Scene opening.'}
                        if stage.startswith('5.') and broken == 'missing_narration':
                            value['narrations'].pop()
                        if stage.startswith('5.') and broken == 'empty_narration':
                            value['narrations'][0] = None
                        if stage == '6.titles' and broken == 'missing_title':
                            value['chapter_titles'].pop()
                        validate(value)
                    return super().generate(stage, instruction, context, check)
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as directory:
                log = RunLog(Path(directory), '{}')
                try:
                    with self.assertRaises(ValueError), redirect_stdout(io.StringIO()):
                        Pipeline(BrokenLLM(), log).run(PLOT)
                finally:
                    log.close()

    def test_budget_cap_and_styles(self):
        writing = writer_plot(PLOT)
        self.assertNotIn('speech_style', writing['characters']['A'])
        self.assertEqual(writing['characters']['A']['background'], 'A_SECRET')
        with self.assertRaises(ValueError):
            validate_turns({'turns': [turn()]}, PLOT['characters'], 2)
        with self.assertRaises(ValueError):
            validate_budgets({'budgets': [1, 2]}, 4, [1, 1])
        story = outline()
        story['target_turns'] = 101
        with self.assertRaises(ValueError):
            validate_outline(story, {**PLOT, 'target_turns': None})
        with self.assertRaises(ValueError):
            validate_plot({**PLOT, 'target_turns': True})

    def test_validation_retry_scope_and_limit_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            log = RunLog(Path(directory), '{}')
            llm = LLM(log, 'http://example.invalid', model='fake', retries=1)
            answers = iter(['{"budgets":[1]}', '{"budgets":[2]}'])
            llm._http = lambda *args: {'choices': [{'message': {'content': next(answers)}, 'finish_reason': 'stop'}]}
            try:
                self.assertEqual(llm.generate('test', 'allocate', {}, lambda v: validate_budgets(v, 2, [1])), {'budgets': [2]})
                llm._http = lambda *args: {'choices': [{'message': {'content': '', 'reasoning_content': 'thinking'},
                                                       'finish_reason': 'length'}],
                                          'usage': {'prompt_tokens': 12000, 'completion_tokens': 4384, 'total_tokens': 16384}}
                with self.assertRaisesRegex(RuntimeError, 'generation/context limit'):
                    llm.generate('test', 'allocate', {}, lambda v: None)
            finally:
                log.close()
            content = log.path.read_text(encoding='utf-8')
            self.assertIn('generation_limit', content)
            self.assertNotIn('全体を再出力', content)
            requests = [e for e in records(content) if e.get('event') == 'llm_request']
            for request in requests:
                self.assertTrue(request['payload']['messages'][0]['content'].isascii())
                self.assertEqual(request['payload']['max_tokens'], 8192)
            self.assertTrue(requests[1]['payload']['messages'][-1]['content'].isascii())
            self.assertEqual(llm.call_count, 3)  # Two format attempts, one limit failure; no limit retry.

    def test_invalid_input_leaves_error_log_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.json'
            source.write_text('{"target_turns":false}', encoding='utf-8')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source), '--output-dir', str(root / 'out')]), 1)
            logs = list((root / 'out').glob('*.log'))
            self.assertEqual(len(logs), 1)
            self.assertEqual(list((root / 'out').glob('*.json')), [])
            self.assertIn('run_error', logs[0].read_text(encoding='utf-8'))

    def test_http_failure_body_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            log = RunLog(Path(directory), '{}')
            llm = LLM(log, 'http://example.invalid/v1')
            failure = urllib.error.HTTPError(llm.base + '/models', 503, 'Unavailable', {}, io.BytesIO(b'model is loading'))
            try:
                with patch('urllib.request.urlopen', side_effect=failure):
                    with self.assertRaisesRegex(RuntimeError, '503'):
                        llm.discover()
            finally:
                log.close()
            self.assertIn('model is loading', log.path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

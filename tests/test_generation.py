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
from plotwell.checkpoint import read_checkpoint, validate_checkpoint
from plotwell.performance import actor_profile
from plotwell.pipeline import Pipeline, writer_plot
from plotwell.runtime import LLM, RunLog
from plotwell.log_format import LogFormat, records
from plotwell.schemas import planning_schema, response_schema
from plotwell.validation import (validate_assignment, validate_budgets, validate_final,
                                 validate_outline, validate_plot, validate_scenes, validate_turns)


PLOT = {'title': 'テスト', 'premise': '二人の会話', 'turn_budget': 12,
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
    return {'title': 'テスト', 'turn_budget': 12, 'opening': '出会う', 'ending': '別れる',
            'chapters': [{'title': f'章{i}', 'synopsis': f'章{i}概要', 'turn_budget': 6, 'scenes': []} for i in (1, 2)]}


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
            value = {'scenes': [{'synopsis': f'シーン{eid}', 'turn_budget': 3,
                                  'time_point': f'時刻{eid}', 'location': f'場所{eid}', 'turns': []} for eid in ids],
                     'scene_events': [[eid] for eid in ids]}
        elif stage.startswith(('3.', '6.')) and not stage.endswith('.review'):
            eid = context['scope']['assigned_events'][0]['id']
            count = [8, 2, 2, 5][eid] if stage.startswith('3.') else context['scene']['turn_budget']
            turns = [turn('A' if i % 2 == 0 else 'B') for i in range(count)]
            turns[-1]['speech'] += f' [E{eid}]'
            value = {'turns': turns, 'continuity_summary': f'出来事{eid}まで完了した。',
                     'visibility': [{'witnesses': ['A'] if i == 0 else ['A', 'B'],
                                     'observation': '目の前で相手が頷く。'} for i in range(count)],
                     'knowledge_updates': ([{'turn': 1, 'actor': 'A', 'subject': 'B',
                                             'fact': 'Bは鍵の場所を知っていた。'}] if eid == 1 else [])}
        elif stage.startswith(('4.', '5.')):
            budgets = [5, 7] if stage.startswith('4.') else ([2, 3] if stage.endswith('.1') else [4, 3])
            value = {'budgets': budgets, 'reasons': ['長い導入の反復を圧縮し、短い転換点の展開を補強する。'] * len(budgets)}
        elif stage.startswith('7.B.'):
            current = context['reference_material']['current_script']
            speech = 'あの、' + current['speech']
            value = {'speech': speech, 'thought': '役者の内面:' + current['speaker']}
        elif stage.startswith('7.A.'):
            value = {'performances': [
                {'turn': int(i), 'speech': 'ええ、' + c['reference_material']['current_script']['speech'],
                 'thought': '演技Aの内面'} for i, c in context['turn_contexts'].items()]}
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
            validate_scenes({'scenes': [{'synopsis': '概要', 'turn_budget': 1, 'time_point': '今',
                                        'location': 'ここ', 'turns': [turn()]}]}, 1)
        for bad in ([[0], [0, 1]], [[1], [0]], [[0], []]):
            with self.assertRaises(ValueError):
                validate_assignment(bad, [0, 1], 2)
        self.assertNotIn('turnは', prompts.OUTLINE + prompts.SCENES)
        self.assertNotIn('milestones', prompts.ACT)
        schema = planning_schema('1.outline')
        self.assertEqual(schema['properties']['story']['properties']['chapters']['items']['properties']['scenes'], {'const': []})
        schema = planning_schema('2.scenes.1')
        self.assertEqual(schema['properties']['scenes']['items']['properties']['turns'], {'const': []})
        schema = response_schema('6.edit.1.1', {'global_reference': PLOT, 'scene': {'turn_budget': 3}})
        self.assertEqual(schema['properties']['turns']['minItems'], 3)
        self.assertEqual(schema['properties']['turns']['maxItems'], 3)
        self.assertEqual(set(schema['properties']['turns']['items']['required']),
                         {'speaker', 'speech', 'thought', 'narration'})

    def test_cli_replay_never_calls_narrator_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = RunLog(root / 'source', json.dumps(PLOT))
            try:
                with redirect_stdout(io.StringIO()):
                    checkpoint = Pipeline(FakeLLM(), log).narrate(PLOT)
            finally:
                log.close()
            for mode in ('A', 'B'):
                llm = FakeLLM()
                llm.discover = lambda: None
                with patch('plotwell.__main__.LLM', return_value=llm), redirect_stdout(io.StringIO()):
                    status = main(['--from-narrator', str(log.path), '--acting-mode', mode,
                                   '--output-dir', str(root / mode), '--env', str(root / 'absent.env')])
                self.assertEqual(status, 0)
                self.assertTrue(all(stage.startswith('7.' + mode + '.') for stage, _, _ in llm.calls))
                output = json.loads(next((root / mode).glob('*.json')).read_text(encoding='utf-8'))
                self.assertEqual(set(output), {'title', 'turn_budget', 'opening', 'chapters', 'ending'})
                result_log = next((root / mode).glob('*.log'))
                self.assertEqual(read_checkpoint(result_log), checkpoint)

    def test_multiple_chapters_events_budgets_knowledge_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = json.dumps(PLOT, ensure_ascii=False, indent=2)
            log = RunLog(Path(directory), raw)
            llm = FakeLLM()
            pipeline = Pipeline(llm, log)
            try:
                checkpoint = pipeline.narrate(PLOT)
                baseline = deepcopy(checkpoint)
                b = pipeline.replay(checkpoint, 'B')
                a = pipeline.replay(checkpoint, 'A')
            finally:
                log.close()
            self.assertEqual(checkpoint, baseline)
            validate_checkpoint(checkpoint)
            self.assertEqual(read_checkpoint(log.path), checkpoint)
            for story in (a, b):
                validate_final(story, PLOT)
                self.assertEqual([c['turn_budget'] for c in story['chapters']], [5, 7])
                self.assertEqual([[s['turn_budget'] for s in c['scenes']] for c in story['chapters']], [[2, 3], [4, 3]])
                for ci, c in enumerate(story['chapters']):
                    for si, s in enumerate(c['scenes']):
                        tokens = [int(v) for t in s['turns'] for v in re.findall(r'\[E(\d+)\]', t['speech'])]
                        self.assertEqual(tokens, [ci * 2 + si])
                        for ti, t in enumerate(s['turns']):
                            source = baseline['story']['chapters'][ci]['scenes'][si]['turns'][ti]
                            self.assertEqual((t['speaker'], t['narration']), (source['speaker'], source['narration']))
                            self.assertNotIn('かもしれない', t['speech'])
            for stage, instruction, context in llm.calls:
                if stage.startswith('2.'):
                    self.assertEqual(context['other_chapter_synopses'], ['章2概要'] if stage.endswith('.1') else ['章1概要'])
                if stage.startswith(('3.draft', '6.edit')) and not stage.endswith('.review'):
                    self.assertNotIn('previous_script', context)
                    self.assertNotIn('following_drafts', context)
                    self.assertNotIn('milestones', context['global_reference'])
                    self.assertNotIn('speech_style', context['global_reference']['characters']['A'])
                if stage.startswith('7.B.') and not stage.endswith('.review'):
                    self.assertLessEqual(len(context['reference_material']['future_script']), 2)
                    self.assertNotIn('SECRET', json.dumps(context))
                    self.assertNotIn('B_PRIVATE' if context['actor']['name'] == 'A' else 'A_PRIVATE', json.dumps(context))
                    if stage == '7.B.2.1.1':
                        self.assertTrue(context['character_knowledge']['acquired_facts'])
                    if stage == '7.B.1.1.2':
                        # B arrived at turn 2: A's turn 1 is not known to B.
                        self.assertEqual(context['character_knowledge']['witnessed_dialogue'], [])
                    if stage == '7.B.1.2.1':
                        self.assertTrue(any('役者の内面:A' == t for t in context['past_thoughts']))
                if stage.startswith('7.A.') and not stage.endswith('.review'):
                    self.assertTrue(all('future_script' not in c['reference_material'] for c in context['turn_contexts'].values()))
                    self.assertNotIn('あの、', json.dumps(context))
                    self.assertNotIn('演技Aの内面', json.dumps(context))
            content = log.path.read_text(encoding='utf-8')
            self.assertTrue(content.startswith(raw + '\n'))
            for event in ('budget_decision', 'narrator_checkpoint'):
                self.assertIn(event, content)
            self.assertFalse(any('.review' in s or s.startswith('7.direct') for s, _, _ in llm.calls))
            # 14 narrator calls, 12 B turns, 8 A actor/scene batches. No review/analysis calls.
            self.assertEqual(len(llm.calls), 34)
            self.assertRegex(log.path.name, r'^\d{8}-\d{6}-\d{3}Z-[a-f0-9]{8}\.log$')
            tampered = deepcopy(checkpoint)
            tampered['story']['title'] = '変更'
            with self.assertRaises(ValueError):
                validate_checkpoint(tampered)

    def test_narrator_checkpoint_does_not_generate_performances(self):
        with tempfile.TemporaryDirectory() as directory:
            log = RunLog(Path(directory), '{}')
            llm = FakeLLM()
            pipeline = Pipeline(llm, log)
            try:
                checkpoint = pipeline.narrate(PLOT)
                self.assertEqual(len(llm.calls), 14)
                self.assertTrue(all(not stage.startswith('7.') for stage, _, _ in llm.calls))
                validate_checkpoint(checkpoint)
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
        story['turn_budget'] = 101
        with self.assertRaises(ValueError):
            validate_outline(story, {**PLOT, 'turn_budget': None})
        with self.assertRaises(ValueError):
            validate_plot({**PLOT, 'turn_budget': True})

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
            self.assertEqual(llm.call_count, 3)  # Two format attempts, one limit failure; no limit retry.

    def test_invalid_input_leaves_error_log_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.json'
            source.write_text('{"turn_budget":false}', encoding='utf-8')
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

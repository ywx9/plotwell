from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback

from .pipeline import Pipeline
from .checkpoint import read_checkpoint
from .runtime import LLM, RunLog, atomic_json, load_env
from .validation import validate_plot


def main(argv=None):
    parser = argparse.ArgumentParser(description='階層的な物語・台本生成（OpenAI互換API）')
    parser.add_argument('plot', type=Path, nargs='?', help='入力plot JSON')
    parser.add_argument('--from-narrator', type=Path, help='確定原稿を持つ.logを再利用し、構成・執筆を行わない')
    parser.add_argument('--acting-mode', choices=('A',), default='A')
    parser.add_argument('--stop-after', choices=('narrator',), help='確定原稿の保存までで終了する')
    parser.add_argument('--output-dir', type=Path, default=Path('data'))
    parser.add_argument('--env', type=Path, default=Path('.env'))
    parser.add_argument('--endpoint', help='例: http://127.0.0.1:9931 または末尾/v1')
    parser.add_argument('--model')
    args = parser.parse_args(argv)
    if bool(args.plot) == bool(args.from_narrator):
        parser.error('plotまたは--from-narratorのどちらか一方を指定してください')
    if args.from_narrator and args.stop_after:
        parser.error('--from-narratorと--stop-afterは併用できません')
    log = None
    try:
        checkpoint = read_checkpoint(args.from_narrator) if args.from_narrator else None
        raw = (json.dumps(checkpoint['plot'], ensure_ascii=False, indent=2) if checkpoint else
               args.plot.read_text(encoding='utf-8-sig'))
        log = RunLog(args.output_dir, raw)
        print(f'log: {log.path}', flush=True)
        log.event('run_start', input_path=str((args.from_narrator or args.plot).resolve()),
                  acting_mode=args.acting_mode, stop_after=args.stop_after)
        plot = json.loads(raw)
        validate_plot(plot)
        load_env(args.env)
        llm = LLM(log, args.endpoint or os.getenv('LLM_ENDPOINT', 'http://127.0.0.1:9931'),
                  model=args.model or os.getenv('LLM_MODEL') or None,
                  api_key=os.getenv('LLM_API_KEY') or None,
                  timeout=float(os.getenv('LLM_TIMEOUT', '300')),
                  max_tokens=int(os.getenv('LLM_MAX_TOKENS', '8192')),
                  retries=int(os.getenv('LLM_RETRIES', '2')),
                  temperature=float(os.getenv('LLM_TEMPERATURE', '0.7')))
        if llm.retries < 0 or llm.timeout <= 0 or llm.max_tokens <= 0:
            raise ValueError('LLM_RETRIES must be nonnegative; timeout/max_tokens must be positive')
        llm.discover()
        pipeline = Pipeline(llm, log)
        if checkpoint:
            story = pipeline.replay(checkpoint, args.acting_mode)
        else:
            story = pipeline.run(plot, args.acting_mode, stop_after_narrator=args.stop_after == 'narrator')
        atomic_json(log.output_path, story)
        log.event('run_complete', output_path=str(log.output_path), total_turns=sum(len(s['turns']) for c in story['chapters'] for s in c['scenes']),
                  target_turns=story['target_turns'],
                  llm_calls=getattr(llm, 'call_count', None), usage=getattr(llm, 'usage_totals', None))
        print(f'json: {log.output_path}', flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        trace = traceback.format_exc()
        if log:
            log.event('run_error', error=str(exc), error_type=type(exc).__name__, traceback=trace)
        print(f'error: {exc}', file=sys.stderr)
        if log:
            print(f'詳細: {log.path}', file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        if log:
            log.close()


if __name__ == '__main__':
    sys.exit(main())

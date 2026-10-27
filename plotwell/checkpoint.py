from copy import deepcopy
import hashlib
import json

from .log_format import LogFormat, records

from .validation import (require, validate_assignment, validate_directing,
                         validate_final, validate_plot)


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def make_checkpoint(plot, story, directing, chapter_events, scene_events):
    value = deepcopy({'version': 4, 'plot': plot, 'story': story, 'directing': directing,
                      'chapter_events': chapter_events, 'scene_events': scene_events})
    value['narrator_hash'] = digest(story)
    value['checksum'] = digest(value)
    validate_checkpoint(value)
    return value


def validate_checkpoint(value):
    require(isinstance(value, dict) and value.get('version') == 4, 'Unsupported narrator checkpoint; regenerate the narrator with the current version')
    require(value.get('checksum') == digest({k: v for k, v in value.items() if k != 'checksum'}),
            'Narrator checkpoint checksum mismatch')
    require(value.get('narrator_hash') == digest(value['story']), 'Narrator script was changed')
    validate_plot(value['plot'])
    validate_final(value['story'], value['plot'])
    chapters = value['story']['chapters']
    require(len(value['directing']) == len(chapters) and len(value['scene_events']) == len(chapters),
            'Checkpoint chapter count mismatch')
    validate_assignment(value['chapter_events'], list(range(len(value['plot'].get('milestones', [])))), len(chapters))
    for ci, chapter in enumerate(chapters):
        require(len(value['directing'][ci]) == len(chapter['scenes']), 'Checkpoint scene count mismatch')
        validate_assignment(value['scene_events'][ci], value['chapter_events'][ci], len(chapter['scenes']))
        for si, scene in enumerate(chapter['scenes']):
            validate_directing({'turns': value['directing'][ci][si]}, scene['turns'], value['plot']['characters'])


def read_checkpoint(path):
    checkpoint = None
    formatter = None
    for event in records(path.read_text(encoding='utf-8-sig')):
        if formatter is None:
            formatter = LogFormat(event)
        if isinstance(event, dict) and event.get('event') == 'narrator_checkpoint':
            checkpoint = formatter.expand(event['checkpoint'])
    require(checkpoint is not None, 'No reusable narrator_checkpoint found in this log')
    if checkpoint.get('version') in (2, 3):
        # Authenticate untouched legacy content before migrating any fields.
        require(checkpoint.get('checksum') == digest({k: v for k, v in checkpoint.items() if k != 'checksum'}),
                'Narrator checkpoint checksum mismatch')
        require(checkpoint.get('narrator_hash') == digest(checkpoint['story']), 'Narrator script was changed')
        plot = deepcopy(checkpoint['plot'])
        story = {k: deepcopy(v) for k, v in checkpoint['story'].items() if k not in ('opening', 'ending')}
        for item in [plot, story] + story['chapters'] + [
                s for c in story['chapters'] for s in c['scenes']]:
            if 'turn_budget' in item:
                require('target_turns' not in item, 'Conflicting legacy target fields')
                item['target_turns'] = item.pop('turn_budget')
        story.setdefault('synopsis', '\n'.join(c['synopsis'] for c in story['chapters']))
        checkpoint = make_checkpoint(plot, story, checkpoint['directing'],
                                     checkpoint['chapter_events'], checkpoint['scene_events'])
    validate_checkpoint(checkpoint)
    return checkpoint

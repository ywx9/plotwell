from __future__ import annotations


def require(condition, message):
    if not condition:
        raise ValueError(message)


def keys(value, expected):
    require(isinstance(value, dict), 'Expected an object')
    require(set(value) == set(expected.split()), f'Expected exactly these keys: {expected}')


def text(value, name, nullable=False):
    require((nullable and value is None) or isinstance(value, str), f'{name} must be text')
    if not nullable:
        require(bool(value.strip()), f'{name} must not be empty')


def positive(value, name):
    require(type(value) is int and value > 0, f'{name} must be a positive integer')


def validate_plot(plot):
    require(isinstance(plot, dict), 'plot must be an object')
    require(not (set(plot) - {'title', 'premise', 'turn_budget', 'milestones', 'characters'}),
            'Unknown plot fields')
    for field in ('title', 'premise'):
        text(plot.get(field), field, nullable=True)
    if plot.get('turn_budget') is not None:
        positive(plot['turn_budget'], 'turn_budget')
    milestones = plot.get('milestones', [])
    require(isinstance(milestones, list), 'milestones must be a list')
    for milestone in milestones:
        text(milestone, 'milestone')
    characters = plot.get('characters')
    require(isinstance(characters, dict), 'characters must be an object')
    require('narrator' in characters, 'characters.narrator is required')
    require(any(name != 'narrator' for name in characters), 'At least one character is required')
    for name, character in characters.items():
        text(name, 'character name')
        require(name not in ('self', 'others'), 'self and others are reserved character names')
        require(isinstance(character, dict), f'{name} must be an object')
        allowed = {'speech_style'} if name == 'narrator' else {
            'speech_style', 'thought_style', 'background', 'how_perceived'}
        require(not set(character) - allowed, f'Unknown fields for {name}')
        for field in allowed - {'how_perceived'}:
            text(character.get(field), f'{name}.{field}', nullable=True)
        perceptions = character.get('how_perceived', {})
        require(isinstance(perceptions, dict), f'{name}.how_perceived must be an object')
        for observer, perception in perceptions.items():
            require(observer in characters or observer in ('self', 'others'),
                    f'Unknown observer: {observer}')
            text(perception, f'{name}.how_perceived.{observer}', nullable=True)


def validate_turns(value, characters, count=None):
    keys(value, 'turns')
    turns = value['turns']
    require(isinstance(turns, list) and bool(turns), 'turns must be a nonempty list')
    if count is not None:
        require(len(turns) == count, f'Expected exactly {count} turns, got {len(turns)}')
    for turn in turns:
        keys(turn, 'speaker speech thought narration')
        require(turn['speaker'] in characters or turn['speaker'] is None, 'Unknown speaker')
        for field in ('speech', 'thought', 'narration'):
            text(turn[field], field, nullable=True)
        require(any(turn[field] for field in ('speech', 'thought', 'narration')), 'Empty turn')
        if turn['speaker'] in (None, 'narrator'):
            require(not turn['speech'] and not turn['thought'],
                    'Narration-only turn must have empty speech and thought')


def validate_outline(story, plot):
    keys(story, 'title turn_budget chapters')
    text(story['title'], 'title')
    if plot.get('title'):
        require(story['title'] == plot['title'], 'Keep the requested title')
    positive(story['turn_budget'], 'story.turn_budget')
    if plot.get('turn_budget') is None:
        require(story['turn_budget'] <= 100, 'Proposed turn_budget must be at most 100')
    else:
        require(story['turn_budget'] == plot['turn_budget'], 'Keep the requested turn_budget')
    require(isinstance(story['chapters'], list) and bool(story['chapters']), 'chapters must be nonempty')
    for chapter in story['chapters']:
        keys(chapter, 'title synopsis turn_budget scenes')
        text(chapter['title'], 'chapter.title')
        text(chapter['synopsis'], 'chapter.synopsis')
        positive(chapter['turn_budget'], 'chapter.turn_budget')
        require(chapter['scenes'] == [], 'Initial scenes must be empty')
    require(sum(c['turn_budget'] for c in story['chapters']) == story['turn_budget'],
            'Chapter budgets must sum to story.turn_budget')


def validate_scenes(value, budget):
    keys(value, 'scenes')
    require(isinstance(value['scenes'], list) and bool(value['scenes']), 'scenes must be nonempty')
    for scene in value['scenes']:
        keys(scene, 'synopsis turn_budget time_point location turns')
        for field in ('synopsis', 'time_point', 'location'):
            text(scene[field], field)
        positive(scene['turn_budget'], 'scene.turn_budget')
        require(scene['turns'] == [], 'Initial turns must be empty')
    require(sum(s['turn_budget'] for s in value['scenes']) == budget,
            'Scene budgets must sum to chapter.turn_budget')


def validate_budgets(value, total, minimums):
    keys(value, 'budgets')
    budgets = value['budgets']
    require(isinstance(budgets, list) and len(budgets) == len(minimums),
            f'Expected {len(minimums)} budgets')
    for budget, minimum in zip(budgets, minimums):
        positive(budget, 'budget')
        require(budget >= minimum, f'Budget must be at least {minimum}')
    require(sum(budgets) == total, f'Budgets must sum to {total}')


def validate_performance(value):
    keys(value, 'speech thought')
    for field in ('speech', 'thought'):
        text(value[field], field, nullable=True)


def validate_assignment(groups, expected, size):
    require(isinstance(groups, list) and len(groups) == size, 'One event group is required per item')
    require(all(isinstance(group, list) for group in groups), 'Event groups must be lists')
    flat = [event for group in groups for event in group]
    require(all(type(event) is int for event in flat), 'Event IDs must be integers')
    require(flat == expected, f'Assign each event once, in order: expected {expected}, got {flat}')


def validate_scene_result(value, characters, count=None):
    keys(value, 'turns continuity_summary visibility knowledge_updates')
    validate_turns({'turns': value['turns']}, characters, count)
    text(value['continuity_summary'], 'continuity_summary')
    require(len(value['continuity_summary']) <= 1000, 'continuity_summary must be <=1000 characters')
    require(isinstance(value['knowledge_updates'], list), 'knowledge_updates must be a list')
    infos = []
    for info in value['visibility']:
        keys(info, 'witnesses observation')
        infos.append({**info, 'learned': []})
    for fact in value['knowledge_updates']:
        keys(fact, 'turn actor subject fact')
        require(type(fact['turn']) is int and 0 <= fact['turn'] < len(infos), 'Invalid knowledge update turn')
        infos[fact['turn']]['learned'].append({k: v for k, v in fact.items() if k != 'turn'})
    validate_directing({'turns': infos}, value['turns'], characters)


def validate_directing(value, turns, characters):
    keys(value, 'turns')
    require(isinstance(value['turns'], list) and len(value['turns']) == len(turns),
            'Directing metadata must correspond to each script turn')
    names = set(characters) - {'narrator'}
    for info, turn in zip(value['turns'], turns):
        keys(info, 'witnesses observation learned')
        require(isinstance(info['witnesses'], list), 'witnesses must be a list')
        require(all(isinstance(n, str) and n in names for n in info['witnesses']), 'Unknown witness')
        require(len(set(info['witnesses'])) == len(info['witnesses']), 'Duplicate witness')
        if turn['speaker'] in names:
            require(turn['speaker'] in info['witnesses'], 'Speaker must witness their own turn')
        text(info['observation'], 'observation', nullable=True)
        require(isinstance(info['learned'], list), 'learned must be a list')
        for fact in info['learned']:
            keys(fact, 'actor subject fact')
            require(fact['actor'] in info['witnesses'], 'Only witnesses can learn a fact')
            require(fact['subject'] in names, 'Unknown fact subject')
            text(fact['fact'], 'fact')


def validate_final(story, plot):
    # Reuse the structural outline checks on a copy without generated children.
    outline = {**story, 'chapters': [{**c, 'scenes': []} for c in story['chapters']]}
    validate_outline(outline, plot)
    for chapter in story['chapters']:
        skeletons = [{**s, 'turns': []} for s in chapter['scenes']]
        validate_scenes({'scenes': skeletons}, chapter['turn_budget'])
        for scene in chapter['scenes']:
            validate_turns({'turns': scene['turns']}, plot['characters'], scene['turn_budget'])

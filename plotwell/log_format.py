"""JSON log records with references to unchanged input text."""

import json


def records(raw):
    decoder = json.JSONDecoder()
    position = 0
    while position < len(raw):
        if raw[position].isspace():
            position += 1
            continue
        try:
            value, end = decoder.raw_decode(raw, position)
        except ValueError:
            # Ignore malformed lines, including an interrupted final record.
            end = raw.find('\n', position)
            if end < 0:
                return
            position = end + 1
            continue
        yield value
        position = end


class LogFormat:
    def __init__(self, plot):
        self.plot = plot
        self.references = {}

        def collect(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    collect(child, path + [key])
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    collect(child, path + [index])
            elif isinstance(value, str) and len(value) >= 40:
                self.references.setdefault(value, path)

        collect(plot, [])

    def compact(self, value, decode_json=False):
        if isinstance(value, dict):
            return {key: self.compact(child, decode_json=key in ('body', 'content'))
                    for key, child in value.items()}
        if isinstance(value, list):
            return [self.compact(child) for child in value]
        if isinstance(value, str):
            if value in self.references:
                return {'$input': self.references[value]}
            # Keep API JSON strings as strings, replacing only repeated input text.
            if not decode_json:
                return value
            try:
                decoded = json.loads(value)
            except ValueError:
                return value
            if isinstance(decoded, (dict, list)):
                return json.dumps(self.compact(decoded), ensure_ascii=False, allow_nan=False)
        return value

    def expand(self, value):
        if isinstance(value, dict):
            if set(value) == {'$input'}:
                result = self.plot
                for key in value['$input']:
                    result = result[key]
                return result
            return {key: self.expand(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self.expand(child) for child in value]
        return value

    def render(self, record):
        return json.dumps(self.compact(record), ensure_ascii=False, allow_nan=False) + '\n'

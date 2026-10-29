#!/usr/bin/env python3
"""Collect sealed legacy XD pages with bounded retries; never arm a capture.

An exchange must return one command's complete response, bounded by its console
prompt. Execution/window anchors bracket every page. No MQTT, VM control or new
wire fields are used. Completeness here precedes the existing analyze.py checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import time

IDENTITY = ('vm_id', 'app_id', 'boot_id', 'agent_instance_id', 'execution_id')
WINDOW = ('schema_version', 'capture_start_us', 'capture_end_us', 'rows',
          'overflow', 'capture_complete', 'transmissions_complete', 'counter_saturated', 'clock_rc', 'task_rc')


class CollectionError(ValueError):
    """A partial collection is evidence, never a successful empty collection."""


def integer(value):
    """Accept the existing integer/decimal-string encoding without coercion."""
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value):
        return int(value)
    raise CollectionError('invalid integer metadata')


def object_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise CollectionError('duplicate JSON key')
        result[key] = value
    return result


def parse_page(text, stream, offset, page_size=4):
    """Validate all original page offsets, row counts, shapes and integer fields."""
    parsed, lines = {}, []
    prefix, width = ('XD_ROW', 21) if stream == 'records' else ('XD_SEND', 10)
    for line in text.splitlines():
        if not line.startswith(('XD_SUMMARY ', 'XD_SESSION ', 'XD_PAGE ', 'XD_IO ', 'XD_CLOCK ', prefix + ' ')):
            continue
        kind, raw = line.split(' ', 1)
        try:
            value = json.loads(raw, object_pairs_hook=object_pairs)
        except (ValueError, TypeError) as error:
            raise CollectionError('invalid ' + kind) from error
        if kind == prefix:
            if not isinstance(value, list) or len(value) != width:
                raise CollectionError('invalid row shape')
            try:
                if any(isinstance(x, bool) or not isinstance(x, (str, int)) for x in value):
                    raise ValueError()
                tuple(int(x) for x in value)
            except (TypeError, ValueError) as error:
                raise CollectionError('invalid row integer') from error
            lines.append(line)
        else:
            if kind in parsed or not isinstance(value, dict):
                raise CollectionError('ambiguous page metadata')
            parsed[kind] = value
    page = parsed.get('XD_PAGE', {})
    if any(type(page.get(k)) is not int for k in ('offset', 'next', 'total')):
        raise CollectionError('missing page metadata')
    end, total = page['next'], page['total']
    if page['offset'] != offset or not 0 <= offset <= end <= total <= 16384:
        raise CollectionError('page offset/total mismatch')
    if end != min(offset + page_size, total) or len(lines) != end - offset:
        raise CollectionError('incomplete page')
    return parsed, lines


def collect(exchange, *, timeout=300.0, attempts=3, clock=time.monotonic):
    """Retry the same read only within one collection deadline and prompt frame.

    A legacy page has no execution token/checksum of its own. Two unchanged
    records-0 anchors bracket it, and its exact rows must match a second read.
    This assumes one owned, serialized console exchange and excludes external
    root modification. It does not prove command delivery or device application.
    """
    if not 0 < timeout <= 3600 or not 1 <= attempts <= 5:
        raise ValueError('invalid collection budget')
    end = clock() + timeout
    evidence = {'complete': False, 'requests': 0, 'retries': 0, 'pages': 0,
                'failure': None, 'session': None, 'window': None}
    output = []

    def query(stream, offset):
        last = None
        for trial in range(attempts):
            if clock() >= end:
                raise CollectionError('collection deadline exhausted')
            evidence['requests'] += 1
            if trial:
                evidence['retries'] += 1
            try:
                response = exchange(f'xenova drive {stream} {offset} 4', end)
                if clock() >= end:
                    raise CollectionError('collection deadline exhausted')
                return parse_page(response, stream, offset)
            except (CollectionError, TimeoutError, OSError) as error:
                last = error
        raise CollectionError('page retry limit: ' + str(last))

    def anchor():
        meta, _ = query('records', 0)
        session, summary = meta.get('XD_SESSION', {}), meta.get('XD_SUMMARY', {})
        if any(not isinstance(session.get(k), str) or not session[k] for k in IDENTITY):
            raise CollectionError('unverified/missing execution identity')
        if any(not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', session[k])
               for k in IDENTITY if k != 'app_id'):
            raise CollectionError('invalid execution identity')
        if any(k not in summary for k in WINDOW):
            raise CollectionError('incomplete capture metadata')
        if summary['capture_complete'] is not True or summary['transmissions_complete'] is not True:
            raise CollectionError('capture/transmissions not sealed')
        if summary['counter_saturated'] is not False or any(integer(summary[k]) for k in ('overflow', 'clock_rc', 'task_rc')):
            raise CollectionError('capture fault/overflow')
        if type(summary['schema_version']) is not int or summary['schema_version'] != 2 or not (
                0 <= integer(summary['capture_start_us']) < integer(summary['capture_end_us'])):
            raise CollectionError('unsupported/invalid capture window')
        if type(summary['rows']) is not int or meta['XD_PAGE']['total'] != summary['rows']:
            raise CollectionError('source total mismatch')
        identity, window = ({k: session[k] for k in IDENTITY}, {k: summary[k] for k in WINDOW})
        if evidence['session'] is None:
            evidence['session'], evidence['window'] = identity, window
            output.extend(['XD_SESSION ' + json.dumps(session), 'XD_SUMMARY ' + json.dumps(summary)])
        elif evidence['session'] != identity or evidence['window'] != window:
            raise CollectionError('execution/capture changed; discard mixed result')
        return meta

    try:
        anchor()
        for stream in ('records', 'transmissions'):
            offset, expected = 0, None
            while True:
                anchor()
                meta, rows = query(stream, offset)
                repeated, repeated_rows = query(stream, offset)
                anchor()
                page = meta['XD_PAGE']
                if page != repeated['XD_PAGE'] or rows != repeated_rows:
                    raise CollectionError('repeated page changed')
                if expected is None:
                    expected = page['total']
                if expected != page['total']:
                    raise CollectionError('page total changed')
                if stream == 'transmissions' and offset == 0:
                    io = meta.get('XD_IO', {})
                    if io.get('transmissions_complete') is not True or integer(io.get('delivery_overflow', -1)):
                        raise CollectionError('transmission accounting incomplete')
                    if io.get('delivery_rows') != expected:
                        raise CollectionError('transmission total mismatch')
                    for kind in ('XD_IO', 'XD_CLOCK'):
                        if kind in meta:
                            output.append(kind + ' ' + json.dumps(meta[kind]))
                output.extend(rows)
                evidence['pages'] += 1
                offset = page['next']
                if offset == expected:
                    break
        anchor()
        evidence['complete'] = True
    except (CollectionError, ValueError, TypeError, OverflowError) as error:
        evidence['failure'] = str(error)
    text = '\n'.join(output) + '\n'
    evidence['sha256'] = hashlib.sha256(text.encode()).hexdigest()
    return evidence, text


class Console:
    """One operator-owned PTY; no login, process control, flush or terminal reset.

    A timeout cannot authorize another command until the old prompt is found.
    The advisory flock only excludes other cooperating collectors, not consoles.
    """
    def __init__(self, path, prompt):
        import fcntl
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            if not os.isatty(self.fd):
                raise ValueError('dedicated console PTY required')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise
        self.prompt, self.ready = prompt, False

    def close(self):
        os.close(self.fd)

    def read_prompt(self, end):
        data = bytearray()
        while time.monotonic() < end:
            left = end - time.monotonic()
            if not select.select([self.fd], [], [], max(0, left))[0]:
                break
            chunk = os.read(self.fd, 4096)
            if not chunk:
                raise CollectionError('console disconnected')
            data.extend(chunk)
            if len(data) > 32768:
                raise CollectionError('console response exceeded 32KiB')
            text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', data.decode('utf-8', errors='strict')).replace('\r', '')
            if text.endswith(self.prompt):
                self.ready = True
                return text
        raise TimeoutError('console prompt stalled; no further command submitted')

    def __call__(self, command, end):
        attempt_end = min(end, time.monotonic() + 5)
        if not self.ready:
            self.read_prompt(attempt_end)
        self.ready = False
        wire = (command + '\r').encode('ascii')
        if os.write(self.fd, wire) != len(wire):
            raise CollectionError('partial console command')
        text = self.read_prompt(attempt_end)
        if command not in text:
            raise CollectionError('missing command echo; response attribution unavailable')
        return text.split(command, 1)[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--console', required=True, help='dedicated operator-owned PTY; initial prompt must be observable')
    parser.add_argument('--prompt', default='uart:~$ ')
    parser.add_argument('--output', type=Path, required=True, help='new evidence directory')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--attempts', type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    console = Console(args.console, args.prompt)
    try:
        report, raw = collect(console, timeout=args.timeout, attempts=args.attempts)
    finally:
        console.close()
    (args.output / ('source.stdout' if report['complete'] else 'source.partial')).write_text(raw, encoding='utf-8')
    (args.output / 'collection.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

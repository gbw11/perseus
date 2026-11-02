"""Legacy page recovery: no fixture stands in for physical PV delivery."""
import importlib.util
import json
from pathlib import Path
import tempfile
import os
import select
import sys
import time
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('drive_collection', ROOT / 'platform/apps/common/xenova-drive-runtime/tools/collect_console.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class Fixture:
    def __init__(self):
        self.session = {key: '11111111-1111-4111-8111-111111111111' for key in collector.IDENTITY}
        self.session['app_id'] = 'manual'
        self.summary = dict(schema_version=2, capture_start_us='1', capture_end_us='200000', rows=6,
                            overflow='0', capture_complete=True, transmissions_complete=True,
                            counter_saturated=False, clock_rc=0, task_rc=0)
        self.io = dict(delivery_rows=6, delivery_overflow='0', transmissions_complete=True)
        self.calls = []
        self.edit = lambda command, text: text

    def __call__(self, command, end):
        self.calls.append(command)
        _, _, stream, offset, count = command.split()
        offset, count = int(offset), int(count)
        prefix, width = ('XD_ROW', 21) if stream == 'records' else ('XD_SEND', 10)
        rows = [[str(i + 1)] * width for i in range(offset, min(6, offset + count))]
        lines = []
        if offset == 0:
            values = [('XD_SUMMARY', self.summary), ('XD_SESSION', self.session)] if stream == 'records' else [('XD_IO', self.io)]
            lines.extend(kind + ' ' + json.dumps(value) for kind, value in values)
        lines.extend(prefix + ' ' + json.dumps(row) for row in rows)
        lines.append('XD_PAGE ' + json.dumps(dict(offset=offset, next=min(6, offset + count), total=6)))
        return self.edit(command, '\n'.join(lines))


class CollectionTest(unittest.TestCase):
    def test_invalid_identity_and_lossy_metadata_coercions_are_rejected(self):
        for key, value in (('capture_start_us', 1.5), ('overflow', False), ('rows', 6.0)):
            with self.subTest(key=key):
                fixture = Fixture();fixture.summary[key] = value
                self.assertFalse(collector.collect(fixture)[0]['complete'])
        fixture = Fixture();fixture.session['execution_id'] = 'unknown'
        self.assertFalse(collector.collect(fixture)[0]['complete'])

    def test_complete_owned_exchange_uses_only_existing_read_commands(self):
        f = Fixture()
        result, raw = collector.collect(f)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['pages'], 4)
        self.assertEqual(raw.count('XD_ROW '), 6)
        self.assertEqual(raw.count('XD_SEND '), 6)
        self.assertEqual(raw.count('XD_SESSION '), 1)
        self.assertTrue(all(c.startswith(('xenova drive records ', 'xenova drive transmissions ')) for c in f.calls))

    def test_small_anchors_keep_full_page_verification_and_identical_output(self):
        fixture = Fixture()
        responses = []
        def exchange(command, end):
            text = fixture(command, end)
            responses.append((command, text))
            return text
        report, raw = collector.collect(exchange)
        self.assertTrue(report['complete'], report)
        self.assertEqual(report['requests'], 18)
        anchors = [text for command, text in responses if command == 'xenova drive records 0 1']
        self.assertEqual(len(anchors), 10)
        self.assertTrue(all(text.count('XD_ROW ') == 1 for text in anchors))
        self.assertEqual(sum(text.count('XD_ROW ') for _, text in responses), 22)
        self.assertEqual(raw.count('XD_ROW '), 6)
        self.assertEqual(raw.count('XD_SEND '), 6)
        # Every actual data page is still read twice at the original limit.
        for stream in ('records', 'transmissions'):
            for offset in (0, 4):
                self.assertEqual(fixture.calls.count(f'xenova drive {stream} {offset} 4'), 2)

    def test_missing_row_retries_original_offset_without_duplicate_output(self):
        f = Fixture()
        failed = False
        def edit(command, text):
            nonlocal failed
            if command == 'xenova drive records 4 4' and not failed:
                failed = True
                return '\n'.join(line for line in text.splitlines() if not line.startswith('XD_ROW '))
            return text
        f.edit = edit
        result, raw = collector.collect(f)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['retries'], 1)
        self.assertEqual(raw.count('XD_ROW '), 6)

    def test_missing_identity_is_not_initial_empty_capture(self):
        f = Fixture()
        f.session['execution_id'] = ''
        result, _ = collector.collect(f)
        self.assertFalse(result['complete'])
        self.assertIn('identity', result['failure'])

    def test_execution_replacement_between_page_and_anchor_is_rejected(self):
        f = Fixture()
        def edit(command, text):
            if command == 'xenova drive records 4 4':
                f.session['boot_id'] = '22222222-2222-4222-8222-222222222222'
            return text
        f.edit = edit
        result, _ = collector.collect(f)
        self.assertFalse(result['complete'])
        self.assertIn('changed', result['failure'])

    def test_new_capture_during_recovery_is_rejected(self):
        f = Fixture()
        def edit(command, text):
            if command == 'xenova drive transmissions 4 4':
                f.summary['capture_start_us'] = '2'
            return text
        f.edit = edit
        self.assertIn('changed', collector.collect(f)[0]['failure'])

    def test_changed_repeated_page_is_not_accepted(self):
        f = Fixture()
        count = 0
        def edit(command, text):
            nonlocal count
            if command == 'xenova drive transmissions 4 4':
                count += 1
                if count == 2:
                    return text.replace('"5"', '"7"')
            return text
        f.edit = edit
        self.assertIn('repeated page changed', collector.collect(f)[0]['failure'])

    def test_transmission_window_not_sealed_is_rejected(self):
        f = Fixture()
        f.summary['transmissions_complete'] = False
        result, _ = collector.collect(f)
        self.assertFalse(result['complete'])
        self.assertIn('not sealed', result['failure'])

    def test_capture_and_delivery_overflow_fail(self):
        for stream in ('source', 'delivery'):
            with self.subTest(stream=stream):
                f = Fixture()
                if stream == 'source': f.summary['overflow'] = '1'
                else: f.io['delivery_overflow'] = '1'
                self.assertFalse(collector.collect(f)[0]['complete'])

    def test_timeout_is_bounded_and_no_new_capture_is_armed(self):
        calls = []
        def blocked(command, end):
            calls.append(command)
            raise TimeoutError('stalled')
        result, _ = collector.collect(blocked, attempts=2)
        self.assertFalse(result['complete'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 1)

    def test_original_collection_deadline_is_not_renewed(self):
        now = [0.0]
        f = Fixture()
        def exchange(command, end):
            self.assertEqual(end, 1.0)
            now[0] += .4
            return f(command, end)
        result, _ = collector.collect(exchange, timeout=1, clock=lambda: now[0])
        self.assertFalse(result['complete'])
        self.assertIn('deadline exhausted', result['failure'])

    def test_truncated_json_wrong_offset_duplicate_metadata_are_rejected(self):
        f = Fixture()
        text = f('xenova drive records 0 4', 10)
        variants = [text + '\nXD_PAGE {}', text.replace('"offset": 0', '"offset": 1'),
                    text.replace('XD_ROW [', 'XD_ROW [INVALID', 1)]
        for value in variants:
            with self.assertRaises(collector.CollectionError):
                collector.parse_page(value, 'records', 0)

    def test_incomplete_rows_cannot_become_empty_success(self):
        f = Fixture()
        f.edit = lambda command, text: '\n'.join(line for line in text.splitlines() if not line.startswith('XD_SEND '))
        result, raw = collector.collect(f, attempts=2)
        self.assertFalse(result['complete'])
        self.assertNotIn('XD_SEND ', raw)


@unittest.skipUnless(sys.platform.startswith('linux'), 'owned POSIX PTY')
class ConsoleFramingTest(unittest.TestCase):
    def setUp(self):
        import pty
        import tty
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.console = collector.Console(os.ttyname(self.slave), 'uart:~$ ')
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.addCleanup(self.console.close)

    def test_no_observed_initial_prompt_means_no_command_submission(self):
        with self.assertRaises(TimeoutError):
            self.console('xenova drive records 0 4', time.monotonic() + .03)
        self.assertFalse(select.select([self.master], [], [], 0)[0])

    def test_timeout_does_not_submit_a_second_command_before_old_prompt(self):
        os.write(self.master, b'uart:~$ ')
        with self.assertRaises(TimeoutError):
            self.console('xenova drive records 0 4', time.monotonic() + .03)
        self.assertEqual(os.read(self.master, 1024), b'xenova drive records 0 4\r')
        with self.assertRaises(TimeoutError):
            self.console('xenova drive records 0 4', time.monotonic() + .03)
        self.assertFalse(select.select([self.master], [], [], 0)[0])

    def test_real_pty_prompt_and_command_echo_frame_each_page(self):
        fixture = Fixture()
        finished = threading.Event()
        failures = []
        def guest():
            try:
                os.write(self.master, b'uart:~$ ')
                pending = bytearray()
                while not finished.is_set():
                    if not select.select([self.master], [], [], .01)[0]:
                        continue
                    pending.extend(os.read(self.master, 1024))
                    while b'\r' in pending:
                        command, _, remaining = pending.partition(b'\r')
                        pending[:] = remaining
                        text = command.decode('ascii')
                        response = fixture(text, 0)
                        os.write(self.master, (text + '\r\n' + response + '\r\nuart:~$ ').encode())
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=guest, daemon=True)
        thread.start()
        try:
            report, _ = collector.collect(self.console, timeout=3)
            self.assertTrue(report['complete'], report)
            self.assertFalse(failures)
        finally:
            finished.set()
            thread.join(timeout=1)


if __name__ == '__main__':
    unittest.main()

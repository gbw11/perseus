"""선택 trace가 잠금·필수 저장·작업 결과의 의미를 바꾸지 않는지 검증한다."""
from pathlib import Path
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'yocto/meta-xenova/recipes-core/xenova-platform/files'))
import xenova_runtime as runtime
import xenova_trace as diagnostic


class TraceTest(unittest.TestCase):
    def test_nested_inclusive_exclusive_and_context_binding(self):
        ticks = [10.0]
        trace = diagnostic.Trace(lambda:ticks[0], {'request_id':'request-1'})
        with trace.activate(), diagnostic.span('outer'):
            ticks[0] += 1
            trace.bind(operation_id='operation-1')
            with diagnostic.span('inner'):
                ticks[0] += 2
            ticks[0] += 3
        report = trace.export()
        self.assertEqual(report['elapsed_seconds'],6)
        self.assertEqual(report['summary']['outer']['inclusive_seconds'],6)
        self.assertEqual(report['summary']['outer']['exclusive_seconds'],4)
        self.assertEqual(report['summary']['inner']['exclusive_seconds'],2)
        self.assertEqual(report['entries'][0]['context']['operation_id'],'operation-1')
        self.assertIsNone(diagnostic.current())

    def test_bounded_entries_and_summary_still_counts_dropped_detail(self):
        trace=diagnostic.Trace(lambda:0,max_events=2)
        with trace.activate():
            for _ in range(20):
                with diagnostic.span('same'):
                    diagnostic.event('attempt',count=1)
        self.assertEqual(len(trace.entries),2)
        self.assertEqual(trace.export()['dropped_entries'],38)
        self.assertEqual(trace.summary['same']['calls'],20)
        with trace.activate():
            for n in range(140):
                with diagnostic.span('different.'+str(n)):
                    pass
        self.assertEqual(len(trace.summary),128)
        self.assertGreater(trace.summary_dropped,0)

    def test_command_counts_survive_dropped_details(self):
        trace=diagnostic.Trace(lambda:0,max_events=0)
        with trace.activate():
            for _ in range(12):
                with diagnostic.span('external.command',command_kind='xl.list'):
                    pass
        self.assertEqual(trace.entries,[])
        self.assertEqual(trace.export()['command_summary']['xl.list']['calls'],12)

    def test_no_argv_path_payload_or_arbitrary_objects_are_serialized(self):
        class Sensitive:
            def __str__(self):
                raise AssertionError('must not stringify untrusted values')
        trace=diagnostic.Trace(context={'request_id':'valid','argv':'secret','vm_id':Sensitive()})
        with trace.activate():
            diagnostic.event('command',command_kind='xl.list',argv=['password'],path='/root/private',
                             payload=Sensitive(),count=1,action='unsafe secret')
            diagnostic.event('password with spaces',count=1)
        rendered=json.dumps(trace.export())
        self.assertNotIn('password',rendered)
        self.assertNotIn('/root',rendered)
        self.assertNotIn('unsafe secret',rendered)
        self.assertEqual(trace.entries[0]['fields'],{'command_kind':'xl.list','count':1})
        clone=trace.export();clone['entries'][0]['fields']['count']=9
        self.assertEqual(trace.entries[0]['fields']['count'],1)

    def test_nested_activation_and_thread_do_not_share_request(self):
        first=diagnostic.Trace();second=diagnostic.Trace()
        with first.activate():
            with second.activate():
                diagnostic.event('second')
            thread=threading.Thread(target=lambda:diagnostic.event('other-thread'))
            thread.start();thread.join()
            diagnostic.event('first')
        self.assertEqual([r['name'] for r in first.entries],['first'])
        self.assertEqual([r['name'] for r in second.entries],['second'])

    def test_decorator_returns_original_value_and_preserves_exception(self):
        @diagnostic.traced('work')
        def work(value):
            if value:
                raise ValueError('business failure')
            return 42
        trace=diagnostic.Trace()
        with trace.activate():
            self.assertEqual(work(False),42)
            with self.assertRaisesRegex(ValueError,'business failure'):
                work(True)
        self.assertEqual(trace.summary['work']['errors'],1)
        self.assertEqual(work.__name__,'work')

    def test_failed_clock_or_collector_does_not_change_wrapped_work(self):
        trace=diagnostic.Trace(clock=lambda:(_ for _ in ()).throw(ValueError('clock')))
        with trace.activate(), diagnostic.span('clock-failed'):
            value=42
        self.assertEqual(value,42)
        self.assertEqual(trace.export()['elapsed_seconds'],0)
        with trace.activate(),patch.object(trace,'event',side_effect=OSError('sink')):
            diagnostic.event('broken')
        with trace.activate(),patch.object(trace,'span',side_effect=OSError('sink')):
            # Construction failures must be noncritical too.
            with diagnostic.span('broken'):
                value=43
        self.assertEqual(value,43)

    def test_stdout_untouched(self):
        output=io.StringIO()
        with redirect_stdout(output),diagnostic.Trace().activate(),diagnostic.span('json'):
            diagnostic.event('safe')
            print('{"ok":true}')
        self.assertEqual(output.getvalue(),'{"ok":true}\n')

    def test_optional_bind_error_and_unknown_fields_are_noncritical(self):
        trace=diagnostic.Trace()
        with trace.activate():
            diagnostic.bind(operation_id='safe-op',payload='secret')
            with patch.object(trace,'bind',side_effect=OSError('optional collector')):
                diagnostic.bind(vm_id='safe-vm')
                value=42
        self.assertEqual(value,42)
        self.assertEqual(trace.context,{'operation_id':'safe-op'})

    def test_runtime_trace_module_is_packaged_with_existing_python_runtime(self):
        recipe=Path(__file__).resolve().parents[1]/'yocto/meta-xenova/recipes-core/xenova-platform/xenova-platform_1.0.bb'
        source=recipe.read_text()
        self.assertIn('file://xenova_trace.py',source)
        self.assertIn('xenova_runtime.py xenova_trace.py',source)


class RuntimeTraceTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def test_atomic_write_keeps_file_and_directory_fsync_order_and_mode(self):
        path=self.root/'required.json';path.write_text('old')
        actions=[];fsync=runtime.os.fsync;replace=runtime.os.replace
        def sync(fd):
            actions.append('directory-fsync' if __import__('stat').S_ISDIR(os.fstat(fd).st_mode) else 'file-fsync')
            return fsync(fd)
        def publish(source,target):
            actions.append('replace');return replace(source,target)
        trace=diagnostic.Trace()
        with trace.activate(),patch.object(runtime.os,'fsync',side_effect=sync),patch.object(runtime.os,'replace',side_effect=publish):
            runtime.atomic_write(path,'new')
        self.assertEqual(path.read_text(),'new')
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(actions,['file-fsync','replace','directory-fsync'])
        for name in ('atomic_write','file_write','file_fsync','replace','directory_fsync'):
            self.assertEqual(trace.summary['storage.'+name]['calls'],1)

    def test_required_file_fsync_failure_is_still_fatal_and_old_value_preserved(self):
        path=self.root/'required.json';path.write_text('old')
        trace=diagnostic.Trace()
        with trace.activate(),patch.object(runtime.os,'fsync',side_effect=OSError('durability failed')):
            with self.assertRaisesRegex(OSError,'durability failed'):
                runtime.atomic_write(path,'new')
        self.assertEqual(path.read_text(),'old')
        self.assertEqual(list(self.root.glob('*.tmp-*')),[])
        self.assertEqual(trace.summary['storage.atomic_write']['errors'],1)
        self.assertNotIn('storage.replace',trace.summary)

    def test_directory_fsync_failure_remains_error_after_rename(self):
        trace=diagnostic.Trace();path=self.root/'required.json'
        with trace.activate(),patch.object(runtime,'sync_directory',side_effect=OSError('directory durability')):
            with self.assertRaisesRegex(OSError,'directory durability'):
                runtime.atomic_write(path,'new')
        self.assertEqual(path.read_text(),'new')
        self.assertEqual(trace.summary['storage.atomic_write']['errors'],1)

    def test_lock_wait_failure_is_not_a_hold_and_tracing_does_not_unlock_owner(self):
        trace=diagnostic.Trace()
        with runtime.operation_lock(self.root/'run'):
            with trace.activate():
                with self.assertRaises(runtime.OperationBusy):
                    with runtime.operation_lock(self.root/'run'):
                        self.fail('contended lock entered')
        self.assertEqual(trace.summary['lock.wait']['errors'],1)
        self.assertNotIn('lock.hold',trace.summary)
        with trace.activate(),runtime.operation_lock(self.root/'run'):
            pass
        self.assertEqual(trace.summary['lock.hold']['calls'],1)


if __name__=='__main__':
    unittest.main()

"""부가 trace의 실제 폐기 비용과 필수 결과/출력·소유 경계의 차이를 확인한다."""
import errno
import threading
import unittest
from unittest.mock import patch
import test_xenova_read_progress as fixtures
import xenova_trace as diagnostic


class DiagnosticCostTest(unittest.TestCase):
    def test_full_trace_does_not_build_discarded_event_fields(self):
        trace=diagnostic.Trace(max_events=0)
        with patch.object(diagnostic,'_fields',wraps=diagnostic._fields) as fields:
            for _ in range(1000):trace.event('expected.pending',count=1)
        self.assertEqual(fields.call_count,0);self.assertEqual(trace.dropped,1000)
        self.assertEqual(trace.entries,[])

    def test_full_trace_keeps_summary_without_building_discarded_rows(self):
        trace=diagnostic.Trace(lambda:0,max_events=0)
        with trace.activate(),patch.object(trace,'_append',wraps=trace._append) as append:
            for _ in range(100):
                with diagnostic.span('external.command',command_kind='xl.list'):pass
        self.assertEqual(append.call_count,0)
        self.assertEqual(trace.summary['external.command']['calls'],100)
        self.assertEqual(trace.command_summary['xl.list']['calls'],100)
        self.assertEqual(trace.dropped,100)

    def case(self):
        c=fixtures.ReadProgressTest('runTest');c.setUp();self.addCleanup(c.doCleanups);return c

    def test_slow_opt_in_trace_export_does_not_hold_common_lock_or_other_stop(self):
        c=self.case();first,other=c.pair();entered,release=threading.Event(),threading.Event();out=[]
        slow=c.m.fork()
        def sink(trace):
            entered.set()
            if not release.wait(10):raise RuntimeError('owned trace sink not released')
        slow.trace_sink=sink
        worker=threading.Thread(target=lambda:out.append(slow.call('get',{'vm_id':first})));worker.start()
        try:
            self.assertTrue(entered.wait(5))
            stopped=c.m.fork().call('stop',{'vm_id':other},idempotency_key='while-trace-blocked',budget_ms=2000)
            self.assertTrue(stopped['ok'],stopped)
            original=c.m.fork().call('control-operation',{'idempotency_key':'while-trace-blocked'})
            self.assertEqual(original['data']['operation']['phase'],'completed')
            self.assertTrue(worker.is_alive())
        finally:release.set();worker.join(15)
        self.assertFalse(worker.is_alive());self.assertTrue(out[0]['ok'],out)

    def test_internal_defect_is_not_input_error_and_required_write_is_not_diagnostic(self):
        c=self.case()
        with patch.object(c.m,'_selection',side_effect=TypeError('owned internal defect')):
            internal=c.m.call('create',c.spec)
        self.assertFalse(internal['ok']);self.assertNotEqual(internal['error']['code'],'INVALID_SPEC')
        self.assertIn('TypeError',internal['error']['message'])
        with patch.object(c.m,'_write',side_effect=OSError(errno.ENOSPC,'owned required write full')):
            required=c.m.call('create',c.spec,execute=True)
        self.assertFalse(required['ok']);self.assertNotEqual(required['error']['code'],'DIAGNOSTIC_UNAVAILABLE')

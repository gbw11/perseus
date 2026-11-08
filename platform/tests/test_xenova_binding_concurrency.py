"""공유 Platform의 병렬 binding 조회가 요청별 기한을 섞지 않는지 검사한다.

실제 관측을 마친 두 호출의 반환 순서만 제어한다. Xen은 foundation 대역을
사용하며 binding 판정, 파일 검증과 publication은 실제 경로를 실행한다.
"""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
import xenova_drive_binding as binding


class BindingConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.f = foundation.FoundationTest('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def overlap_observations(self):
        """A/B 관측 뒤 A부터 반환시켜 중첩 호출의 복구 순서를 고정한다."""
        observed = {name: threading.Event() for name in ('A', 'B')}
        release = {name: threading.Event() for name in ('A', 'B')}
        request = threading.local()
        original = binding._observe

        def hold_result(*args, **kwargs):
            result = original(*args, **kwargs)
            name = request.name
            observed[name].set()
            if not release[name].wait(5):
                raise AssertionError('binding result release timed out')
            return result

        def run(name):
            request.name = name
            return binding.observe(self.f.p, self.f.vm_id)

        with patch.object(binding, '_observe', side_effect=hold_result):
            with ThreadPoolExecutor(max_workers=2) as executor:
                try:
                    first = executor.submit(run, 'A')
                    self.assertTrue(observed['A'].wait(5), 'first observation did not complete')
                    second = executor.submit(run, 'B')
                    self.assertTrue(observed['B'].wait(5), 'second observation did not complete')
                    release['A'].set()
                    first_row = first.result(timeout=5)
                    release['B'].set()
                    second_row = second.result(timeout=5)
                finally:
                    release['A'].set()
                    release['B'].set()
        self.assertEqual(first_row['status'], 'observed')
        self.assertEqual(second_row['status'], 'observed')
        self.assertEqual(first_row['execution_id'], second_row['execution_id'])

    def test_parallel_observations_leave_no_deadline_for_the_next_round(self):
        view = self.f.start()
        self.assertFalse(hasattr(self.f.p, 'response_deadline'))
        self.overlap_observations()
        self.assertFalse(hasattr(self.f.p, 'response_deadline'))

        self.f.ticks = 3
        row = binding.observe(self.f.p, self.f.vm_id)
        self.assertEqual(row['status'], 'observed')
        self.assertEqual(row['execution_id'], view['execution_id'])
        self.assertFalse(hasattr(self.f.p, 'response_deadline'))

    def test_parallel_observations_preserve_the_callers_existing_deadline(self):
        view = self.f.start()
        self.f.p.response_deadline = 10
        self.overlap_observations()
        self.assertEqual(self.f.p.response_deadline, 10)

        self.f.ticks = 3
        row = binding.observe(self.f.p, self.f.vm_id)
        self.assertEqual(row['status'], 'observed')
        self.assertEqual(row['execution_id'], view['execution_id'])
        self.assertEqual(self.f.p.response_deadline, 10)

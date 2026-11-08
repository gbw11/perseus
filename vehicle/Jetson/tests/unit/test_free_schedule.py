"""필요 corridor 우선순위·원 age·유한 순환·갱신 불능 보고를 합성 값으로 확인한다.

선택 셀 수는 certificate 성공 수가 아니다. 필요 mask/주기/지연 상한은 명시 입력이며
실제 차량 제동거리나 Nano 처리율·retained scene 사용권을 수락하지 않는다.
"""

from dataclasses import FrozenInstanceError
import unittest

from xenova_vehicle.mapping.free_schedule import FreeScheduler
from xenova_vehicle.mapping.local_map import FREE, OCCUPIED, GridSpec


CONTEXT = ('map', 'a' * 64, 'clock')


def fields(width=8):
    import numpy as np
    return dict(context_key=CONTEXT, needed_mask=np.ones((1, width), dtype=bool),
        support=np.ones((1, width), dtype=bool), permission=np.ones((1, width), dtype=bool),
        state=np.zeros((1, width), dtype=np.uint8), observed_us=np.zeros((1, width), dtype=np.uint64),
        time_known=np.zeros((1, width), dtype=bool), now_us=1000000, capture_lower_us=950000,
        max_cells=2, max_ray_tests=100, cycle_bound_us=100000, completion_bound_us=0)


def scheduler(width=8):
    return FreeScheduler(GridSpec(width, 1, .1, (0., 0.)), context_key=CONTEXT)


class FreeScheduleTests(unittest.TestCase):
    def test_imminent_required_precedes_new_or_unproven_then_surrounding(self):
        import numpy as np
        owner, data = scheduler(), fields()
        data['needed_mask'][:] = False
        data['needed_mask'][0, :3] = True
        data['state'][0, [0, 1, 3]] = FREE
        data['observed_us'][0, [0, 1, 3]] = [501000, 900000, 900000]
        data['time_known'][0, [0, 1, 3]] = True
        data['max_cells'] = 1
        first = owner.plan(**data)
        self.assertEqual(first.cells, ((0, 0),))
        data['needed_mask'][0, 3] = True
        data['max_cells'] = 3
        result = owner.plan(**data)
        self.assertEqual(result.cells, ((0, 0), (2, 0), (3, 0)))
        self.assertEqual(result.priorities, ('REQUIRED_EXPIRING', 'REQUIRED_NEW_OR_UNPROVEN', 'REQUIRED_NEW_OR_UNPROVEN'))
        self.assertFalse(result.all_needed_live_fresh)
        self.assertFalse(result.renewal_proven)
        self.assertEqual(result.ray_budget, data['max_ray_tests'])
        self.assertEqual(len(result.cells), data['max_cells'])
        self.assertTrue(np.all(data['state'][0, [0, 1, 3]] == FREE))

    def test_unknown_attempts_rotate_across_whole_needed_set_without_claiming_success(self):
        owner, data = scheduler(), fields()
        selected = [owner.plan(**data) for _ in range(4)]
        self.assertEqual(tuple(cell for result in selected for cell in result.cells), tuple((i, 0) for i in range(8)))
        for result in selected:
            self.assertFalse(result.all_needed_live_fresh)
            self.assertFalse(result.renewal_proven)
            self.assertEqual(result.minimum_refresh_cycles, 4)
            self.assertEqual(result.due_not_scheduled_count, 6)
            self.assertTrue(result.necessary_capacity_ok)
            self.assertEqual(result.status, 'NECESSARY_CAPACITY_ONLY')

    def test_budget_failure_and_no_ray_budget_are_explicit(self):
        owner, data = scheduler(), fields()
        data['cycle_bound_us'] = 200000
        result = owner.plan(**data)
        self.assertEqual(result.status, 'BUDGET_INSUFFICIENT')
        self.assertFalse(result.necessary_capacity_ok)
        data['max_ray_tests'] = 0
        result = owner.plan(**data)
        self.assertEqual(result.cells, ())
        self.assertEqual(result.ray_budget, 0)
        self.assertEqual(result.due_not_scheduled_count, 8)
        self.assertFalse(result.renewal_proven)

    def test_support_permission_and_retained_cannot_supply_missing_live_free(self):
        owner, data = scheduler(), fields()
        data['support'][0, 0] = False
        data['permission'][0, 1] = False
        result = owner.plan(**data)
        self.assertEqual(result.unavailable_needed_count, 2)
        self.assertEqual(result.status, 'REQUIRED_REGION_NOT_ELIGIBLE')
        self.assertFalse(any(cell in ((0, 0), (1, 0)) for cell in result.cells))
        data['support'][:] = data['permission'][:] = True
        # 과거 free의 시각만으로 현재 UNKNOWN을 FREE로 취급하지 않는다.
        data['observed_us'][:] = 950000
        data['time_known'][:] = True
        self.assertFalse(owner.plan(**data).all_needed_live_fresh)
        data['state'][0, 0] = OCCUPIED
        self.assertEqual(owner.plan(**data).status, 'REQUIRED_REGION_OCCUPIED')

    def test_missing_needed_mask_and_capture_time_preserve_unknown_with_finite_surrounding_cursor(self):
        owner, data = scheduler(), fields()
        data['needed_mask'] = None
        results = [owner.plan(**data) for _ in range(4)]
        self.assertEqual(tuple(cell for result in results for cell in result.cells), tuple((i, 0) for i in range(8)))
        self.assertTrue(all(result.status == 'NEEDED_REGION_UNKNOWN' and not result.renewal_proven for result in results))
        data['needed_mask'] = fields()['needed_mask']
        data['capture_lower_us'] = None
        self.assertEqual(owner.plan(**data).status, 'CAPTURE_TIME_UNKNOWN')

    def test_uint64_original_age_boundary_and_public_bounds(self):
        import numpy as np
        owner, data = scheduler(4), fields(4)
        now = (1 << 63) + 1000000
        data.update(now_us=now, capture_lower_us=now - 10000, max_cells=4)
        data['state'][:] = FREE
        data['time_known'][:] = True
        data['observed_us'][0] = [now - 500001, now - 500000, now - 100000, now + 1]
        result = owner.plan(**data)
        self.assertFalse(result.all_needed_live_fresh)
        self.assertEqual(result.cells[:2], ((0, 0), (1, 0)))
        with self.assertRaises(FrozenInstanceError):
            result.cells = ()
        data['state'][0, 0] = 255
        with self.assertRaises(ValueError):
            owner.plan(**data)
        data['state'][0, 0] = FREE
        data['observed_us'] = data['observed_us'].astype(np.float64)
        with self.assertRaises(ValueError):
            owner.plan(**data)
        with self.assertRaises(ValueError):
            FreeScheduler(GridSpec(128, 128, .1), context_key=CONTEXT)

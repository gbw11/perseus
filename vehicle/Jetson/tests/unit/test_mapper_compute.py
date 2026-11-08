"""주입한 processor/clock으로 M 계산 owner의 사건 순서와 상한만 검산한다.

SDK/arena/actor를 열지 않는다. 실제 Nano 지연·GIL·센서 freshness 수락 시험이 아니며
event gate로 시험 thread를 반드시 회수한다. 실제 실행 환경은 검증 보고서에 기록한다.
"""

import json
import threading
import time
import unittest

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.yolop_frames import NNInputStamp, NNPacket, RuntimeTensor
from xenova_vehicle.runtime.local_envelope import decode_structure
from xenova_vehicle.runtime.mapper_compute import (
    MapperCompute, MapperComputeError, MapperInput, MapperStillAlive, MAX_PHASE_EVENTS,
)
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.resident_transport import SentPacket


def identifier(number):
    return '00000000-0000-4000-8000-' + f'{number:012x}'


class Clock:
    def __init__(self, now=1000):
        self.now = now
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def set(self, now):
        with self.lock:
            self.now = now


def sample(sequence=1, nn=False):
    rgb = RawFrame('rgb_video', sequence, 800, 900, 1000, 2, 2, 'NV12', bytes(6), 10)
    depth = RawFrame('depth_right', sequence + 10, 801, 901, 1000, 2, 2, 'RAW16_LE', bytes(8), None)
    packet = original = None
    names = ['rgb_video', 'depth_right']
    if nn:
        tensors = (
            RuntimeTensor('det_out', 0, 0x321, 3, (1, 6300, 6), (75600, 12, 2), 0),
            RuntimeTensor('drive_area_seg', 0, 0x4321, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 75600),
            RuntimeTensor('lane_line_seg', 0, 0x4321, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 485200),
        )
        packet = NNPacket('nn_output', sequence, 800, 900, 1000, bytes(894800), tensors, 'A', golden={'match': True})
        original = NNInputStamp('nn_input', sequence, 800, 900, 1000, 320, 320, 'BGR888p', 'A')
        names.extend(('nn_input', 'nn_output'))
    clocks = {name: dict(bridge_sample={'j_before_us': '1000', 'sdk_us': '900', 'j_after_us': '1000'},
                        sdk_host_timestamp_j_bracket_us=['1000', '1000']) for name in names}
    return CaptureSample(rgb, depth, original, packet, clocks)


def item(number=1, *, sequence=None, nn=False, generation='A', expires=500_000, parser_cap=None):
    return MapperInput(identifier(number), generation, sample(number if sequence is None else sequence, nn),
        expires, parser_cap_us=parser_cap, context_bytes=b'opaque-original-context')


def sent(event, *, sequence=None, completed=None, stage='PARSER_STARTED'):
    stamp = event.started_us
    fields = dict(version=1, service_instance=identifier(100), channel_epoch=identifier(101),
        sender_instance=identifier(102), message_sequence='1', kind='PROGRESS', binding_domain='LIFECYCLE',
        binding_revision='0', request_id=event.job_id, issued_j_us=str(stamp),
        expires_j_us=str(event.expires_us), payload=dict(stage=stage,
            source_sequence=str(event.nn_sequence if sequence is None else sequence),
            completed_j_us=str(stamp), state='WARMUP', reason=None))
    packet = decode_structure(json.dumps(fields, separators=(',', ':')).encode('ascii'))
    return SentPacket(packet, stamp if completed is None else completed, stamp)


def wait_until(predicate):
    end = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('bounded test gate did not advance')
        time.sleep(.002)


def event_of(owner, kind, collected=None):
    collected = [] if collected is None else collected
    found = []

    def receive():
        events = owner.poll()
        collected.extend(events)
        found.extend(event for event in events if event.kind == kind)
        return bool(found)

    wait_until(receive)
    return found[-1]


class Processor:
    def __init__(self, *, enter=None, leave=None, geometry_gate=None, parse=None, geometry=None):
        self.enter, self.leave, self.gate = enter, leave, geometry_gate
        self.parse_function, self.geometry_function = parse, geometry
        self.parsed = []
        self.geometries = []

    def parse(self, original, checkpoint):
        self.parsed.append(original.job_id)
        if self.parse_function is not None:
            return self.parse_function(original, checkpoint)
        return original.sample.nn_output

    def geometry(self, original, parsed, checkpoint):
        self.geometries.append(original.job_id)
        if self.enter is not None:
            self.enter.set()
        if self.gate is not None and not self.gate.wait(2):
            raise AssertionError('test geometry gate expired')
        if self.leave is not None:
            self.leave.set()
        if self.geometry_function is not None:
            return self.geometry_function(original, parsed, checkpoint)
        checkpoint()
        return original.job_id.encode('ascii')


class MapperComputeTests(unittest.TestCase):
    def cleanup(self, owner, *gates):
        owner.request_stop()
        for gate in gates:
            gate.set()
        owner.join(2)

    def test_input_detaches_mutable_metadata_without_minting_capture_time(self):
        original = sample(nn=True)
        value = MapperInput(identifier(1), 'A', original, 2000)
        original.clocks['rgb_video']['sdk_host_timestamp_j_bracket_us'][0] = '0'
        original.nn_output.golden['match'] = False
        detached = value.sample
        self.assertEqual(detached.clocks['rgb_video']['sdk_host_timestamp_j_bracket_us'], ['1000', '1000'])
        self.assertTrue(detached.nn_output.golden['match'])
        detached.clocks['rgb_video']['sdk_host_timestamp_j_bracket_us'][0] = '2'
        self.assertEqual(value.sample.clocks['rgb_video']['sdk_host_timestamp_j_bracket_us'][0], '1000')
        self.assertIs(value.sample.nn_output, value.sample.nn_output)
        self.assertFalse(value.fresh(999))
        self.assertTrue(value.fresh(1999))
        self.assertFalse(value.fresh(2000))
        self.assertIsNone(value.sample.depth.exposure_us)
        with self.assertRaises(MapperComputeError):
            MapperInput(identifier(2), 'A', original, 2000, context_bytes=bytearray(b'x'))

    def test_parser_waits_for_actual_send_and_nn_completion_precedes_geometry(self):
        clock, entered, gate = Clock(), threading.Event(), threading.Event()

        def parse(original, checkpoint):
            clock.set(1100)
            return original.sample.nn_output

        processor = Processor(enter=entered, geometry_gate=gate, parse=parse)
        owner = MapperCompute(lambda generation: processor, source_generation='A', clock=clock)
        owner.start()
        try:
            original = item(nn=True)
            self.assertTrue(owner.submit(original))
            request = event_of(owner, 'PARSER_START_REQUIRED')
            self.assertEqual(processor.parsed, [])
            self.assertFalse(owner.acknowledge_parser_start(identifier(99), sent(request)))
            with self.assertRaises(MapperComputeError):
                owner.acknowledge_parser_start(original.job_id, sent(request, sequence=99))
            self.assertEqual(processor.parsed, [])
            self.assertTrue(owner.acknowledge_parser_start(original.job_id, sent(request)))
            completed = event_of(owner, 'NN_COMPLETED')
            self.assertTrue(entered.wait(2))
            self.assertEqual((completed.started_us, completed.decode_started_us, completed.completed_us), (1000, 1000, 1100))
            self.assertTrue(completed.accepted)
            self.assertEqual(completed.nn_source_key, (1, 800, 900, 1000))
            self.assertIsNone(owner.take_result())
            clock.set(1200)
            gate.set()
            event_of(owner, 'GEOMETRY_COMPLETED')
            result = owner.take_result()
            self.assertEqual(result.completed_us, 1200)
            self.assertEqual(result.payload, original.job_id.encode('ascii'))
        finally:
            self.cleanup(owner, gate)

    def test_original_parser_cap_is_not_extended_by_delayed_send_ack(self):
        clock, processor = Clock(), Processor()
        owner = MapperCompute(lambda generation: processor, source_generation='A', clock=clock)
        owner.start()
        try:
            original = item(nn=True, parser_cap=1500)
            owner.submit(original)
            request = event_of(owner, 'PARSER_START_REQUIRED')
            self.assertEqual(request.parser_deadline_us, 1500)
            clock.set(1500)
            self.assertFalse(owner.acknowledge_parser_start(original.job_id, sent(request, completed=1500)))
            event = event_of(owner, 'CANCELLED')
            self.assertFalse(event.accepted)
            self.assertEqual(processor.parsed, [])
            self.assertIsNone(owner.take_result())
        finally:
            self.cleanup(owner)

    def test_latest_raw_and_nn_have_separate_bounded_fair_pending_slots(self):
        entered, gate, processor = threading.Event(), threading.Event(), Processor()
        processor.enter, processor.gate = entered, gate
        owner = MapperCompute(lambda generation: processor, source_generation='A', clock=Clock())
        owner.start()
        try:
            first = item(1, nn=True)
            owner.submit(first)
            request = event_of(owner, 'PARSER_START_REQUIRED')
            owner.acknowledge_parser_start(first.job_id, sent(request))
            self.assertTrue(entered.wait(2))
            event_of(owner, 'NN_COMPLETED')
            for original in (item(2), item(3), item(4, nn=True), item(5, nn=True)):
                self.assertTrue(owner.submit(original))
            state = owner.snapshot()
            self.assertEqual((state['active'], state['pending_raw'], state['pending_nn']), (first.job_id, True, True))
            self.assertEqual(state['counts']['pending_replaced'], 2)
            gate.set()
            second = event_of(owner, 'PARSER_START_REQUIRED')
            self.assertEqual(second.job_id, identifier(5))
            owner.acknowledge_parser_start(second.job_id, sent(second))
            event_of(owner, 'GEOMETRY_COMPLETED')
            wait_until(lambda: len(processor.geometries) == 3)
            self.assertEqual(processor.geometries, [identifier(1), identifier(3), identifier(5)])
            self.assertEqual(processor.parsed, [identifier(1), identifier(5)])
        finally:
            self.cleanup(owner, gate)

    def test_source_a_b_a_fences_old_job_and_serializes_mutable_owner_reset(self):
        entered, left, gate = threading.Event(), threading.Event(), threading.Event()
        old = Processor(enter=entered, leave=left, geometry_gate=gate)
        new = Processor()
        factories = []

        def factory(generation):
            factories.append((generation, left.is_set()))
            return old if len(factories) == 1 else new

        owner = MapperCompute(factory, source_generation='A', clock=Clock())
        owner.start()
        try:
            owner.submit(item(1))
            self.assertTrue(entered.wait(2))
            owner.replace_source('B')
            owner.replace_source('A')
            owner.submit(item(2))
            self.assertEqual(factories, [('A', False)])
            gate.set()
            event_of(owner, 'GEOMETRY_COMPLETED')
            result = owner.take_result()
            self.assertEqual((result.job_id, result.generation_serial), (identifier(2), 3))
            self.assertEqual(factories, [('A', False), ('A', True)])
        finally:
            self.cleanup(owner, gate)

    def test_stop_is_immediate_fence_but_join_reports_actual_still_alive(self):
        entered, gate = threading.Event(), threading.Event()
        owner = MapperCompute(lambda generation: Processor(enter=entered, geometry_gate=gate),
            source_generation='A', clock=Clock())
        owner.start()
        try:
            owner.submit(item())
            self.assertTrue(entered.wait(2))
            owner.submit(item(2))
            owner.request_stop()
            self.assertFalse(owner.snapshot()['pending_raw'])
            with self.assertRaises(MapperComputeError):
                owner.submit(item(3))
            with self.assertRaises(MapperStillAlive):
                owner.join(0)
            self.assertTrue(owner.is_alive)
            self.assertIsNone(owner.take_result())
        finally:
            self.cleanup(owner, gate)
        self.assertFalse(owner.is_alive)
        with self.assertRaises(MapperComputeError):
            owner.start()

    def test_source_expiry_at_real_completion_and_delivery_removes_result_authority(self):
        clock = Clock()

        def late(original, parsed, checkpoint):
            clock.set(2000)
            return b'late'

        owner = MapperCompute(lambda generation: Processor(geometry=late), source_generation='A', clock=clock)
        owner.start()
        try:
            owner.submit(item(expires=2000))
            cancelled = event_of(owner, 'CANCELLED')
            self.assertEqual(cancelled.completed_us, 2000)
            self.assertIsNone(owner.take_result())
        finally:
            self.cleanup(owner)
        clock.set(1000)
        owner = MapperCompute(lambda generation: Processor(), source_generation='A', clock=clock)
        owner.start()
        try:
            owner.submit(item(expires=2000))
            event_of(owner, 'GEOMETRY_COMPLETED')
            clock.set(2000)
            result = owner.take_result()
            self.assertFalse(result.accepted)
            self.assertIsNone(result.payload)
            self.assertEqual(result.completed_us, 1000)
        finally:
            self.cleanup(owner)

    def test_coalesced_progress_preserves_job_and_geometry_terminal_events(self):
        def flood(original, parsed, checkpoint):
            for _ in range(MAX_PHASE_EVENTS + 1):
                checkpoint('BLOCK_COMPLETED')
            return b'unreachable'

        owner = MapperCompute(lambda generation: Processor(geometry=flood), source_generation='A', clock=Clock())
        owner.start()
        try:
            owner.submit(item())
            wait_until(lambda: owner.snapshot()['result_pending'] and owner.snapshot()['active'] is None)
            events = owner.poll()
            self.assertEqual([event.kind for event in events], ['JOB_STARTED', 'PROGRESS', 'GEOMETRY_COMPLETED'])
            self.assertEqual(owner.snapshot()['counts']['progress_coalesced'], MAX_PHASE_EVENTS)
            self.assertTrue(owner.take_result().accepted)
            self.assertIsNone(owner.terminal_error)
        finally:
            self.cleanup(owner)

    def test_skip_duplicate_nn_does_not_decode_or_emit_parser_success(self):
        processor = Processor()
        owner = MapperCompute(lambda generation: processor, source_generation='A', clock=Clock())
        owner.start()
        try:
            original = item(nn=True)
            owner.submit(original)
            request = event_of(owner, 'PARSER_START_REQUIRED')
            self.assertEqual(request.nn_source_key, (1, 800, 900, 1000))
            self.assertTrue(owner.skip_parser(original.job_id))
            self.assertFalse(owner.skip_parser(original.job_id))
            events = []
            event_of(owner, 'GEOMETRY_COMPLETED', events)
            self.assertEqual(processor.parsed, [])
            self.assertEqual(processor.geometries, [original.job_id])
            self.assertFalse(any(event.kind == 'NN_COMPLETED' for event in events))
            self.assertEqual(owner.take_result().expires_us, original.expires_us)
            self.assertEqual(owner.snapshot()['counts']['parser_skipped'], 1)
        finally:
            self.cleanup(owner)

    def test_accepted_swap_runs_on_compute_owner_and_mutable_result_is_rejected(self):
        owner_thread, swaps = threading.current_thread(), []

        class TransactionProcessor(Processor):
            def accepted(self, original):
                swaps.append((original.job_id, threading.current_thread()))

        processor = TransactionProcessor()
        owner = MapperCompute(lambda generation: processor, source_generation='A', clock=Clock())
        owner.start()
        try:
            owner.submit(item())
            event_of(owner, 'GEOMETRY_COMPLETED')
            self.assertTrue(owner.take_result().accepted)
            self.assertEqual(len(swaps), 1)
            self.assertEqual(swaps[0][0], identifier(1))
            self.assertIsNot(swaps[0][1], owner_thread)
            self.assertFalse(swaps[0][1].daemon)
        finally:
            self.cleanup(owner)
        owner = MapperCompute(lambda generation: Processor(geometry=lambda original, parsed, checkpoint: bytearray(b'mutable')),
            source_generation='A', clock=Clock())
        owner.start()
        try:
            owner.submit(item())
            wait_until(lambda: owner.terminal_error is not None)
            self.assertEqual(owner.terminal_error, 'MapperComputeError')
            self.assertLessEqual(owner.snapshot()['phase_events'], MAX_PHASE_EVENTS)
            self.assertIsNone(owner.take_result())
        finally:
            self.cleanup(owner)

    def test_ordinary_candidate_expiry_preserves_previously_accepted_processor(self):
        clock, factories, cancelled, committed = Clock(), [], [], ['prior-accepted-state']

        class CandidateProcessor(Processor):
            def geometry(self, original, parsed, checkpoint):
                if original.job_id == identifier(1):
                    clock.set(1200)
                checkpoint()
                self.staged = original.job_id
                return b'committed'

            def cancelled(self, original, reason):
                cancelled.append((original.job_id, reason))
                self.staged = None

            def accepted(self, original):
                committed.append(self.staged)
                self.staged = None

        processor = CandidateProcessor()

        def factory(generation):
            factories.append(generation)
            return processor

        owner = MapperCompute(factory, source_generation='A', clock=clock)
        owner.start()
        try:
            owner.submit(item(1, expires=1200))
            event_of(owner, 'CANCELLED')
            owner.submit(item(2, expires=2000))
            event_of(owner, 'GEOMETRY_COMPLETED')
            self.assertEqual(factories, ['A'])
            self.assertEqual(cancelled, [(identifier(1), 'SOURCE_EXPIRED')])
            self.assertEqual(committed, ['prior-accepted-state', identifier(2)])
            self.assertTrue(owner.take_result().accepted)
        finally:
            self.cleanup(owner)

    def test_delayed_main_keeps_job_start_terminal_and_latest_progress_in_causal_order(self):
        first_enter, second_enter, first_gate, second_gate = (threading.Event() for _ in range(4))

        def geometry(original, parsed, checkpoint):
            checkpoint('BLOCK_COMPLETED')
            entered, gate = ((first_enter, first_gate) if original.job_id == identifier(1)
                else (second_enter, second_gate))
            entered.set()
            if not gate.wait(2):
                raise AssertionError('delayed-main test gate expired')
            checkpoint()
            return b'complete'

        owner = MapperCompute(lambda generation: Processor(geometry=geometry), source_generation='A', clock=Clock())
        owner.start()
        try:
            owner.submit(item(1))
            self.assertTrue(first_enter.wait(2))
            owner.submit(item(2))
            first_gate.set()
            self.assertTrue(second_enter.wait(2))
            events = owner.poll()
            self.assertEqual([(event.kind, event.job_id) for event in events], [
                ('JOB_STARTED', identifier(1)), ('GEOMETRY_COMPLETED', identifier(1)),
                ('JOB_STARTED', identifier(2)), ('PROGRESS', identifier(2))])
        finally:
            self.cleanup(owner, first_gate, second_gate)


if __name__ == '__main__':
    unittest.main()

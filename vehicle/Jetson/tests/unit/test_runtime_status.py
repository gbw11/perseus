"""mapper-runtime-1 원 진단 값의 소유권·exact 문법·원 시각만 검사한다.

실제 worker/모델/clock/HTTP/MQTT 준비를 만드는 시험이 아니다. 합성 ID와 원
숫자 fixture를 사용하며 stage/residency의 독립 진술을 준비 권한으로 해석하지 않는다.
"""

from copy import deepcopy
import json
import unittest
from uuid import UUID

from xenova_vehicle.runtime.lifecycle_policy import ROLE_STATES
from xenova_vehicle.runtime.runtime_status import (
    MAX_PAYLOAD_BYTES, RuntimeStatus, RuntimeStatusError, U64_MAX,
)


def identifier(number):
    return str(UUID(int=number))


def status_fields():
    return dict(profile='mapper-runtime-1', vehicle='rc-vehicle', device='jetson-01',
        service_instance=identifier(1), mapper_boot=identifier(2),
        mapper_instance=identifier(3), pipeline_instance=identifier(4),
        clock_epoch=identifier(5), release_sha256='a' * 64,
        runtime_state='RUNNING', source_binding_revision='7', snapshot_j_us='1000',
        capture_ready=True, metric_ready=False,
        model=dict(requirement='REQUIRED', blob_sha256='b' * 64,
            residency_evidence='PAIRED_OUTPUT', inference_state='UNAVAILABLE',
            parser_state='VALID', last_input_j_us=None, last_result_j_us='900'),
        workers=[dict(role=role, instance=identifier(index + 10), state=state,
            last_progress_sequence=str(index + 1), last_progress_j_us='800')
            for index, (role, state) in enumerate((('A', 'STREAMING'), ('M', 'WAIT_CLOCK'), ('D', 'IDLE')))],
        reason=None)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


class RuntimeStatusTests(unittest.TestCase):
    def reject(self, value):
        with self.assertRaises(RuntimeStatusError):
            RuntimeStatus(value)

    def test_canonical_original_values_are_immutable_and_fields_are_independent(self):
        fields = status_fields()
        original = deepcopy(fields)
        value = RuntimeStatus(fields)
        expected = json.dumps(original, sort_keys=True, ensure_ascii=True,
            separators=(',', ':'), allow_nan=False).encode('ascii')
        self.assertEqual(value.canonical_bytes, expected)
        fields['model']['parser_state'] = 'FAILED'
        fields['workers'].clear()
        first, second = value.fields, value.fields
        self.assertIsNot(first, second)
        self.assertIsNot(first['model'], second['model'])
        self.assertIsNot(first['workers'][0], second['workers'][0])
        first['model']['last_result_j_us'] = '1000'
        first['workers'][0]['last_progress_j_us'] = '1000'
        self.assertEqual(value.fields, original)
        self.assertEqual(value.canonical_bytes, expected)
        self.assertEqual(RuntimeStatus.from_bytes(expected), value)
        self.assertEqual(RuntimeStatus(dict(reversed(tuple(original.items())))), value)
        with self.assertRaises(AttributeError):
            value._canonical_bytes = b'{}'

    def test_missing_id_time_and_independent_model_evidence_remain_null_without_defaults(self):
        fields = status_fields()
        for key in ('service_instance', 'mapper_boot', 'mapper_instance', 'pipeline_instance',
                    'clock_epoch', 'release_sha256', 'source_binding_revision'):
            fields[key] = None
        fields.update(runtime_state='STARTING', capture_ready=False, metric_ready=False, workers=[])
        fields['model'].update(requirement='ABSENT', blob_sha256=None,
            residency_evidence='GRAPH_STARTED', inference_state='FAILED', parser_state='UNAVAILABLE',
            last_input_j_us=None, last_result_j_us=None)
        self.assertEqual(RuntimeStatus(fields).fields, fields)
        # 형식만으로 model stage/residency의 의미 상관관계나 준비 권한을 발명하지 않는다.
        fields['model'].update(requirement='OPTIONAL', residency_evidence='PAIRED_OUTPUT')
        self.assertEqual(RuntimeStatus(fields).fields['model'], fields['model'])

    def test_exact_fields_at_every_level_reject_missing_unknown_and_wrong_container(self):
        for path in ((), ('model',), ('workers', 0)):
            fields = status_fields()
            node = fields
            for key in path:
                node = node[key]
            for key in tuple(node):
                changed = deepcopy(fields)
                target = changed
                for parent in path:
                    target = target[parent]
                del target[key]
                with self.subTest(path=path, missing=key):
                    self.reject(changed)
            node['extra'] = None
            self.reject(fields)
        for key, wrong in (('model', []), ('workers', {}), ('workers', ())):
            fields = status_fields()
            fields[key] = wrong
            self.reject(fields)
        self.reject([])
        self.reject({'profile': 'mapper-runtime-1'})

    def test_wire_rejects_duplicate_keys_nonfinite_numbers_and_invalid_json(self):
        raw = encoded(status_fields())
        cases = (
            raw.replace(b'"profile":', b'"profile":"mapper-runtime-1","profile":', 1),
            raw.replace(b'"requirement":', b'"requirement":"REQUIRED","requirement":', 1),
            raw.replace(b'"instance":', b'"instance":null,"instance":', 1),
            raw.replace(b'"metric_ready":false', b'"metric_ready":NaN'),
            raw.replace(b'"metric_ready":false', b'"metric_ready":Infinity'),
            raw.replace(b'"metric_ready":false', b'"metric_ready":-Infinity'),
            raw.replace(b'"snapshot_j_us":"1000"', b'"snapshot_j_us":1000'),
            raw.replace(b'"snapshot_j_us":"1000"', b'"snapshot_j_us":1e3'),
            b'', b'{} trailing', b'[]', b'\xff', b'\xef\xbb\xbf' + raw,
            b'[' * 1100 + b']' * 1100,
        )
        for changed in cases:
            with self.subTest(raw=changed[:70]):
                with self.assertRaises(RuntimeStatusError):
                    RuntimeStatus.from_bytes(changed)

    def test_builtin_projection_rejects_callbacks_subclasses_and_cyclic_input(self):
        class Trap:
            def __str__(self):
                raise AssertionError('custom conversion must not run')

            def __iter__(self):
                raise AssertionError('custom iteration must not run')

            def __deepcopy__(self, memo):
                raise AssertionError('custom copy must not run')

        class DictTrap(dict):
            def items(self):
                raise AssertionError('custom mapping must not run')

        class StringTrap(str):
            def __str__(self):
                raise AssertionError('custom string conversion must not run')

        self.reject(DictTrap(status_fields()))
        self.reject(Trap())
        for key in ('vehicle', 'model', 'workers', 'capture_ready'):
            fields = status_fields()
            fields[key] = Trap()
            self.reject(fields)
        fields = status_fields()
        fields['reason'] = StringTrap('UNREGISTERED')
        self.reject(fields)
        fields = status_fields()
        fields['model'] = fields
        self.reject(fields)
        fields = status_fields()
        fields['workers'].append(fields['workers'])
        self.reject(fields)
        for wrong in (bytearray(encoded(status_fields())), memoryview(encoded(status_fields())), None):
            with self.assertRaises(RuntimeStatusError):
                RuntimeStatus.from_bytes(wrong)

    def test_ascii_limits_and_device_topic_grammar_are_exact(self):
        fields = status_fields()
        fields.update(vehicle='"' * 64, device='a' + '_' * 31, reason='R' * 32)
        self.assertEqual(RuntimeStatus(fields).fields, fields)
        for key, wrongs in (
            ('vehicle', ('', 'x' * 65, '차량', 'a\n', '\x00')),
            ('device', ('', 'a' * 33, 'Jetson', '_jetson', 'a/b', 'a+', 'a#', 'a b')),
            ('reason', ('', 'R' * 33, '오류', 'a\r', '\x7f')),
        ):
            for wrong in wrongs:
                changed = deepcopy(fields)
                changed[key] = wrong
                with self.subTest(key=key, wrong=wrong):
                    self.reject(changed)

    def test_uuid_sha_null_and_noncanonical_fake_identifiers(self):
        for key in ('service_instance', 'mapper_boot', 'mapper_instance', 'pipeline_instance', 'clock_epoch'):
            for wrong in ('', identifier(0), '0', identifier(10).upper(), identifier(10).replace('-', ''), False):
                fields = status_fields()
                fields[key] = wrong
                with self.subTest(key=key, wrong=wrong):
                    self.reject(fields)
        for wrong in (identifier(0), 'missing', 0):
            fields = status_fields()
            fields['workers'][0]['instance'] = wrong
            self.reject(fields)
        fields = status_fields()
        fields['workers'][0]['instance'] = None
        self.assertIsNone(RuntimeStatus(fields).fields['workers'][0]['instance'])
        for path in (('release_sha256',), ('model', 'blob_sha256')):
            for wrong in ('F' * 64, 'f' * 63, 'f' * 65, 'g' * 64, 0):
                fields = status_fields()
                target = fields if len(path) == 1 else fields['model']
                target[path[-1]] = wrong
                self.reject(fields)

    def test_role_states_and_order_reuse_current_lifecycle_enums(self):
        fields = status_fields()
        for state in ROLE_STATES['S']:
            fields['runtime_state'] = state
            self.assertEqual(RuntimeStatus(fields).fields['runtime_state'], state)
        for index, role in enumerate(('A', 'M', 'D')):
            for state in ROLE_STATES[role]:
                fields['workers'][index]['state'] = state
                self.assertEqual(RuntimeStatus(fields).fields['workers'][index]['state'], state)
        for workers in ([], [status_fields()['workers'][1]],
                        [status_fields()['workers'][0], status_fields()['workers'][2]]):
            fields['workers'] = workers
            self.assertEqual(RuntimeStatus(fields).fields['workers'], workers)
        for workers in (list(reversed(status_fields()['workers'])),
                        [status_fields()['workers'][0]] * 2,
                        status_fields()['workers'] + [status_fields()['workers'][0]]):
            fields['workers'] = workers
            self.reject(fields)
        for key, wrong in (('runtime_state', 'PREPARE'), ('profile', 'mapper-runtime-2')):
            fields = status_fields()
            fields[key] = wrong
            self.reject(fields)
        fields = status_fields()
        fields['workers'][0]['state'] = 'IDLE'
        self.reject(fields)
        fields = status_fields()
        fields['workers'][0]['role'] = 'S'
        self.reject(fields)

    def test_model_stage_requirement_and_residency_allowlists_are_independent(self):
        for key, allowed in (
            ('requirement', ('REQUIRED', 'OPTIONAL', 'ABSENT')),
            ('residency_evidence', ('NONE', 'GRAPH_STARTED', 'PAIRED_OUTPUT')),
            ('inference_state', ('UNAVAILABLE', 'WARMUP', 'VALID', 'STALE', 'FAILED')),
            ('parser_state', ('UNAVAILABLE', 'WARMUP', 'VALID', 'STALE', 'FAILED')),
        ):
            for state in allowed:
                fields = status_fields()
                fields['model'][key] = state
                self.assertEqual(RuntimeStatus(fields).fields['model'][key], state)
            for wrong in ('READY', 'valid', '', None, False):
                fields = status_fields()
                fields['model'][key] = wrong
                self.reject(fields)

    def test_exact_bools_and_full_u64_decimal_range_do_not_use_u63_or_native_numbers(self):
        fields = status_fields()
        maximum = str(U64_MAX)
        fields.update(source_binding_revision=maximum, snapshot_j_us=maximum)
        fields['model'].update(last_input_j_us=maximum, last_result_j_us='0')
        for worker in fields['workers']:
            worker.update(last_progress_sequence=maximum, last_progress_j_us=maximum)
        self.assertEqual(RuntimeStatus(fields).fields, fields)
        for key in ('capture_ready', 'metric_ready'):
            for wrong in (0, 1, 'true', None, 1.0):
                changed = deepcopy(fields)
                changed[key] = wrong
                self.reject(changed)
        for key in ('source_binding_revision', 'snapshot_j_us'):
            for wrong in (False, True, 0, 1000, 1.0, float('nan'), float('inf'),
                          '-1', '+1', '01', ' 1', '1.0', str(U64_MAX + 1)):
                changed = deepcopy(fields)
                changed[key] = wrong
                with self.subTest(key=key, wrong=wrong):
                    self.reject(changed)
        fields['snapshot_j_us'] = None
        self.reject(fields)

    def test_original_times_cannot_be_future_or_retimed_and_progress_pair_is_preserved(self):
        original = status_fields()
        for key in ('last_input_j_us', 'last_result_j_us'):
            fields = deepcopy(original)
            fields['model'][key] = '1001'
            self.reject(fields)
        for sequence, completed in (('2', '1001'), (None, '800'), ('2', None)):
            fields = deepcopy(original)
            fields['workers'][0].update(last_progress_sequence=sequence, last_progress_j_us=completed)
            self.reject(fields)
        fields = deepcopy(original)
        fields['workers'][0].update(last_progress_sequence=None, last_progress_j_us=None)
        fields['model'].update(last_input_j_us='1000', last_result_j_us='0')
        self.assertEqual(RuntimeStatus(fields).fields, fields)
        value = RuntimeStatus(original)
        for _ in range(3):
            self.assertEqual(RuntimeStatus.from_bytes(value.canonical_bytes).fields, original)
        self.assertEqual(value.fields['snapshot_j_us'], '1000')
        self.assertEqual(value.fields['workers'][0]['last_progress_j_us'], '800')

    def test_raw_payload_cap_is_checked_before_parse_and_canonical_bytes_stay_bounded(self):
        value = RuntimeStatus(status_fields())
        raw = value.canonical_bytes
        self.assertLess(len(raw), MAX_PAYLOAD_BYTES)
        padded = raw + b' ' * (MAX_PAYLOAD_BYTES - len(raw))
        self.assertEqual(len(padded), MAX_PAYLOAD_BYTES)
        self.assertEqual(RuntimeStatus.from_bytes(padded), value)
        with self.assertRaises(RuntimeStatusError):
            RuntimeStatus.from_bytes(padded + b' ')
        fields = status_fields()
        fields.update(vehicle='"' * 64, device='a' + '_' * 31, reason='R' * 32,
            source_binding_revision=str(U64_MAX), snapshot_j_us=str(U64_MAX))
        fields['model'].update(last_input_j_us=str(U64_MAX), last_result_j_us=str(U64_MAX))
        for worker in fields['workers']:
            worker.update(last_progress_sequence=str(U64_MAX), last_progress_j_us=str(U64_MAX))
        self.assertLessEqual(len(RuntimeStatus(fields).canonical_bytes), MAX_PAYLOAD_BYTES)

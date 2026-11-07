"""자원 manifest의 exact 문법·원 bytes·유한 소유 값을 검사한다.

합성 PID·이름·runtime·episode는 실제 OS 관측이나 생성 허가가 아니다. guard의
증거 판정은 별도 시험에 맡기며 파일·IPC·프로세스 조회를 실행하지 않는다.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import patch

from xenova_vehicle.runtime import resource_manifest as records
from resource_guard_fixtures import encode, full_fields, identifier, minimal_fields, pending_fields, runtime


MAX = 2**64 - 1
PID_MAX = 2**32 - 1
SHM_SLOTS = ('RAW_CURRENT', 'RAW_RETIRED', 'DB_COPY_0', 'DB_COPY_1', 'DB_COPY_2', 'DB_COPY_3')
SEM_SLOTS = ('RAW_0_WRITABLE', 'RAW_0_READY', 'RAW_1_WRITABLE', 'RAW_1_READY', 'DB_WRITABLE', 'DB_READY')
SLOTS = SHM_SLOTS + SEM_SLOTS
ROLES = ('S', 'A', 'M', 'D', 'TRACKER')


class ResourceManifestTests(unittest.TestCase):
    def parse(self, fields):
        return records.ResourceManifest(encode(fields))

    def rejected(self, fields):
        with self.assertRaises(records.ManifestError):
            self.parse(fields)

    def maximum_fields(self):
        value = minimal_fields()
        for key in ('pid_ns_inode', 'mnt_ns_inode', 'shm_mount_id', 'shm_dev', 'shm_root_inode'):
            value['runtime'][key] = str(MAX)
        value['revision'] = str(MAX)
        value['state'] = 'CLEANUP_CANDIDATE'
        value['owners'] = []
        for role in ROLES:
            for phase in ('CURRENT', 'RETIRED'):
                if role == 'S' and phase == 'RETIRED':
                    continue
                value['owners'].append(dict(role=role, phase=phase, pid=PID_MAX - len(value['owners']),
                                            start_ticks=str(MAX)))
        value['cgroup'] = dict(mode='SYSTEMD_V1', path='/' + '\\' * 191,
                               device=str(MAX), inode=str(MAX))
        for index, slot in enumerate(SHM_SLOTS):
            object_id = identifier(100 + index)
            name = ('/xnv_raw_' if slot.startswith('RAW') else '/xnv_db_') + object_id
            value['resources'][slot] = dict(state='KNOWN', name=name, object_id=object_id, size_bytes=str(MAX))
        for index, slot in enumerate(SEM_SLOTS):
            value['resources'][slot] = dict(state='KNOWN', name='/' + 's' * 126 + str(index),
                                            object_id=None, size_bytes=None)
        planned = value['resources']['RAW_CURRENT']
        value['resources']['RAW_CURRENT'] = dict(state='UNKNOWN', name=None, object_id=None, size_bytes=None)
        value['intents'] = [dict(intent_id=identifier(500), slot='RAW_CURRENT', operation='CREATE',
                                 planned_name=planned['name'], planned_object_id=planned['object_id'],
                                 planned_size_bytes=planned['size_bytes'])]
        value['recovery_episode'] = dict(episode_id=identifier(600), service_attempts=5,
                                         role_attempts=dict(A=5, M=5, D=5))
        return value

    def test_minimal_from_zero_does_not_require_six_completed_semaphores(self):
        manifest = self.parse(minimal_fields())
        self.assertTrue(manifest.complete_inventory)
        self.assertEqual(len(manifest.resources), 12)
        self.assertEqual(manifest.known_resources, ())
        self.assertEqual(len(manifest.known_owners), 1)
        self.assertEqual(manifest.known_owners[0].role, 'S')
        self.assertEqual(manifest.intents, ())
        self.assertTrue(all(resource.state == 'NOT_ATTEMPTED' for resource in manifest.resources))

    def test_full_and_all_nonpending_top_states_are_grammar_values_not_native_proof(self):
        for state in ('DIRTY', 'CLEANUP_CANDIDATE', 'CLEAN'):
            with self.subTest(state=state):
                fields = full_fields()
                fields['state'] = state
                manifest = self.parse(fields)
                self.assertTrue(manifest.complete_inventory)
                self.assertEqual(manifest.state, state)
                self.assertEqual(len(manifest.known_resources), 12)

    def test_pending_and_unknown_shm_or_semaphore_are_incomplete(self):
        for state in ('INTENT', 'UNKNOWN'):
            for slot in ('RAW_CURRENT', 'DB_COPY_0', 'RAW_0_READY'):
                with self.subTest(state=state, slot=slot):
                    manifest = self.parse(pending_fields(state=state, slot=slot))
                    self.assertFalse(manifest.complete_inventory)
                    self.assertEqual(len(manifest.intents), 1)
                    self.assertEqual(manifest.resource(slot).state, state)
                    self.assertEqual(manifest.intents[0].slot, slot)
                    if slot in SEM_SLOTS:
                        self.assertIsNone(manifest.intents[0].planned_name)

    def test_declared_maximum_width_is_valid_4319_bytes(self):
        manifest = self.parse(self.maximum_fields())
        self.assertEqual(len(manifest.canonical_bytes), 4319)
        self.assertEqual(len(manifest.owners), 9)
        self.assertEqual(len(manifest.resources), 12)
        self.assertFalse(manifest.complete_inventory)
        self.assertEqual(manifest.resource('RAW_CURRENT').state, 'UNKNOWN')
        self.assertEqual(manifest.recovery_episode.service_attempts, 5)
        self.assertEqual(manifest.recovery_episode.role_attempts, (5, 5, 5))

    def test_canonical_key_and_owner_order_preserve_original_raw_hash(self):
        fields = full_fields()
        fields['owners'].reverse()
        raw = json.dumps(dict(reversed(tuple(fields.items()))), ensure_ascii=True, indent=1).encode('ascii')
        manifest = records.ResourceManifest(raw)
        normalized = deepcopy(fields)
        normalized['owners'].sort(key=lambda o: (ROLES.index(o['role']), ('CURRENT', 'RETIRED').index(o['phase'])))
        self.assertEqual(manifest.raw_bytes, raw)
        self.assertEqual(manifest.raw_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest.canonical_bytes, encode(normalized))
        self.assertEqual(manifest.canonical_sha256, hashlib.sha256(encode(normalized)).hexdigest())
        self.assertNotEqual(manifest.raw_sha256, manifest.canonical_sha256)
        self.assertEqual(manifest.ref.raw_sha256, manifest.raw_sha256)
        self.assertEqual(manifest.ref.runtime, manifest.runtime)
        self.assertEqual(manifest.ref.revision, manifest.revision)
        self.assertEqual(manifest.ref.service_instance, manifest.service_instance)
        self.assertEqual(manifest.ref.resource_epoch, manifest.resource_epoch)

    def test_raw_8192_allows_whitespace_without_increasing_canonical_body(self):
        canonical = encode(minimal_fields())
        raw = canonical[:-2] + b' ' * (8192 - len(canonical)) + b'}\n'
        manifest = records.ResourceManifest(raw)
        self.assertEqual(len(raw), 8192)
        self.assertEqual(manifest.canonical_bytes, canonical)
        self.assertEqual(manifest.raw_sha256, hashlib.sha256(raw).hexdigest())
        with self.assertRaises(records.ManifestError):
            records.ResourceManifest(raw[:-2] + b' }\n')

    def test_empty_oversize_and_overdeep_raw_are_rejected_before_json_allocation(self):
        for raw in (b'', b' ' * 8193, b'{"bad":[[[0]]]}'):
            with self.subTest(raw_length=len(raw)):
                with patch('json.loads', side_effect=AssertionError('JSON parser reached too early')):
                    with self.assertRaises(records.ManifestError):
                        records.ResourceManifest(raw)

    def test_native_tree_node_limit_precedes_typed_schema_projection(self):
        leaves = {chr(97 + i): 0 for i in range(8)}
        raw = json.dumps({chr(65 + i): [leaves] * 9 for i in range(9)},
                         separators=(',', ':')).encode('ascii')
        self.assertLessEqual(len(raw), 8192)
        # 깊이3/폭12/list9 안에서 node512만 넘기므로 typed schema보다 먼저 제한해야 한다.
        with patch.object(records, '_parse', side_effect=AssertionError('typed projection reached before node cap')):
            with self.assertRaises(records.ManifestError):
                records.ResourceManifest(raw)

    def test_raw_ascii_syntax_trailing_and_number_forms_are_strict(self):
        valid = encode(minimal_fields())
        for raw in (b'\xef\xbb\xbf' + valid, b'\xff', valid + b'{}', valid[:-2],
                    b'{"version":NaN}', b'{"version":Infinity}', b'{"version":1.0}',
                    b'{"version":' + b'1' * 21 + b'}'):
            with self.subTest(raw_prefix=raw[:30]):
                with self.assertRaises(records.ManifestError):
                    records.ResourceManifest(raw)

    def test_nested_duplicate_and_escaped_duplicate_keys_are_rejected(self):
        fields = minimal_fields()
        fields['recovery_episode']['role_attempts']['A'] = 0
        raw = encode(fields)
        needles = (b'"version":1', b'"shm_fs":"tmpfs"', b'"state":"NOT_ATTEMPTED"', b'"A":0')
        for needle in needles:
            with self.subTest(needle=needle):
                self.assertIn(needle, raw)
                with self.assertRaises(records.ManifestError):
                    records.ResourceManifest(raw.replace(needle, needle + b',' + needle, 1))
        escaped = raw.replace(b'"version":1', b'"version":1,"\\u0076ersion":1', 1)
        with self.assertRaises(records.ManifestError):
            records.ResourceManifest(escaped)

    def test_missing_unknown_keys_at_every_object_layer_are_rejected(self):
        paths = ((), ('runtime',), ('owners', 0), ('cgroup',), ('resources',),
                 ('resources', 'RAW_CURRENT'), ('intents', 0),
                 ('recovery_episode',), ('recovery_episode', 'role_attempts'))
        for path in paths:
            base = pending_fields()
            target = base
            for key in path:
                target = target[key]
            for missing in tuple(target):
                with self.subTest(path=path, missing=missing):
                    fields = deepcopy(base)
                    node = fields
                    for key in path:
                        node = node[key]
                    del node[missing]
                    self.rejected(fields)
            with self.subTest(path=path, unknown=True):
                target['unknown'] = None
                self.rejected(base)

    def test_uuid_version_state_and_runtime_profile_fields_are_exact(self):
        base = minimal_fields()
        for name in ('service_instance', 'resource_epoch'):
            for wrong in (None, '01234567-89AB-CDEF-8123-456789ABCDEF', 'bad'):
                with self.subTest(name=name, wrong=wrong):
                    fields = deepcopy(base)
                    fields[name] = wrong
                    self.rejected(fields)
        for name, wrong in (('version', True), ('version', 1.0), ('version', '1'),
                            ('version', 2), ('state', 'UNKNOWN')):
            with self.subTest(name=name, wrong=wrong):
                fields = deepcopy(base)
                fields[name] = wrong
                self.rejected(fields)
        for name, wrong in (('boot_id', 'bad'), ('shm_fs', 'ext4'), ('shm_fs', 'TMPFS')):
            with self.subTest(runtime_field=name):
                fields = deepcopy(base)
                fields['runtime'][name] = wrong
                self.rejected(fields)

    def test_u64_wire_is_canonical_string_and_positive_where_required(self):
        base = minimal_fields()
        for wrong in (1, True, '01', '+1', '-1', '1 ', str(MAX + 1), '0'):
            with self.subTest(wrong=wrong):
                fields = deepcopy(base)
                fields['revision'] = wrong
                self.rejected(fields)
                fields = deepcopy(base)
                fields['runtime']['shm_dev'] = wrong
                self.rejected(fields)
        fields = deepcopy(base)
        fields['revision'] = str(MAX)
        fields['owners'][0]['start_ticks'] = '0'
        fields['cgroup']['device'] = '0'
        manifest = self.parse(fields)
        self.assertEqual(manifest.revision, MAX)
        self.assertEqual(manifest.owners[0].start_ticks, 0)
        self.assertEqual(manifest.cgroup.device, 0)

    def test_missing_optional_owner_and_unknown_birth_are_different(self):
        fields = minimal_fields()
        self.assertTrue(self.parse(fields).complete_inventory)
        fields['owners'].append(dict(role='A', phase='CURRENT', pid=None, start_ticks=None))
        manifest = self.parse(fields)
        self.assertFalse(manifest.complete_inventory)
        self.assertIsNone(next(o for o in manifest.owners if o.role == 'A').birth)
        self.assertEqual(len(manifest.known_owners), 1)
        fields['state'] = 'CLEAN'
        self.rejected(fields)

    def test_current_s_is_required_and_has_a_known_birth(self):
        base = minimal_fields()
        for owners in ([], [dict(role='S', phase='CURRENT', pid=None, start_ticks=None)],
                       [dict(role='S', phase='RETIRED', pid=1, start_ticks='0')]):
            with self.subTest(owners=owners):
                fields = deepcopy(base)
                fields['owners'] = owners
                self.rejected(fields)

    def test_owner_role_phase_birth_pair_and_count_are_bounded(self):
        base = minimal_fields()
        bad = (dict(role='UNKNOWN', phase='CURRENT', pid=3, start_ticks='0'),
               dict(role='A', phase='UNKNOWN', pid=3, start_ticks='0'),
               dict(role='A', phase='CURRENT', pid=None, start_ticks='0'),
               dict(role='A', phase='CURRENT', pid=3, start_ticks=None),
               dict(role='A', phase='CURRENT', pid=True, start_ticks='0'),
               dict(role='A', phase='CURRENT', pid=0, start_ticks='0'),
               dict(role='A', phase='CURRENT', pid=PID_MAX + 1, start_ticks='0'))
        for owner in bad:
            with self.subTest(owner=owner):
                fields = deepcopy(base)
                fields['owners'].append(owner)
                self.rejected(fields)
        fields = self.maximum_fields()
        fields['owners'].append(deepcopy(fields['owners'][0]))
        self.rejected(fields)

    def test_owner_slot_and_known_birth_are_independently_unique(self):
        fields = minimal_fields()
        first = fields['owners'][0]
        fields['owners'].append(dict(role='A', phase='CURRENT', pid=first['pid'], start_ticks=first['start_ticks']))
        self.rejected(fields)
        fields['owners'][-1]['start_ticks'] = str(int(first['start_ticks']) + 1)
        self.assertTrue(self.parse(fields).complete_inventory)
        fields['owners'].append(dict(role='A', phase='CURRENT', pid=PID_MAX, start_ticks=str(MAX)))
        self.rejected(fields)

    def test_cgroup_mode_path_encoding_and_scalar_bounds(self):
        base = minimal_fields()
        for mode in ('SYSTEMD_V1', 'UNIFIED_V2'):
            fields = deepcopy(base)
            fields['cgroup']['mode'] = mode
            fields['cgroup']['path'] = '/' + '\\' * 191
            self.assertEqual(len(self.parse(fields).cgroup.path), 192)
        for path in ('', 'relative', '/.', '/a/../b', '/a/./b', '/a\x00', '/a\n', '/a\x7f', '/' + 'a' * 192, '/\uac00'):
            with self.subTest(path=path):
                fields = deepcopy(base)
                fields['cgroup']['path'] = path
                self.rejected(fields)
        for name, wrong in (('mode', 'CGROUP'), ('device', 0), ('inode', '0')):
            fields = deepcopy(base)
            fields['cgroup'][name] = wrong
            self.rejected(fields)

    def test_all_inventory_addresses_exist_and_lookup_uses_that_exact_slot(self):
        manifest = self.parse(full_fields())
        self.assertIs(type(manifest.resources), tuple)
        self.assertEqual({r.slot for r in manifest.resources}, set(SLOTS))
        for resource in manifest.resources:
            self.assertEqual(manifest.resource(resource.slot), resource)
        for wrong in ('UNKNOWN', 0, None):
            with self.subTest(wrong=wrong):
                with self.assertRaises(records.ManifestError):
                    manifest.resource(wrong)

    def test_nonknown_resource_cannot_carry_actual_name_object_or_size(self):
        for state in ('NOT_ATTEMPTED', 'INTENT', 'UNKNOWN'):
            for name, wrong in (('name', '/x'), ('object_id', identifier(800)), ('size_bytes', '1')):
                with self.subTest(state=state, name=name):
                    fields = minimal_fields() if state == 'NOT_ATTEMPTED' else pending_fields(state=state)
                    fields['resources']['RAW_CURRENT'][name] = wrong
                    self.rejected(fields)

    def test_known_shm_name_uuid_size_and_semaphore_nulls(self):
        base = full_fields()
        for name, wrong in (('name', '/wrong'), ('object_id', None), ('size_bytes', None),
                            ('size_bytes', '0'), ('size_bytes', 1)):
            with self.subTest(kind='SHM', name=name):
                fields = deepcopy(base)
                fields['resources']['RAW_CURRENT'][name] = wrong
                self.rejected(fields)
        for name, wrong in (('name', None), ('object_id', identifier(801)), ('size_bytes', '1')):
            with self.subTest(kind='SEM', name=name):
                fields = deepcopy(base)
                fields['resources']['RAW_0_READY'][name] = wrong
                self.rejected(fields)

    def test_canonical_posix_names_do_not_accept_paths_or_dot_aliases(self):
        base = full_fields()
        for name in ('', '/', '//abc', '/a/b', '/.', '/..', '/a b', '/a\\b', '/a\n', '/' + 's' * 128):
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields['resources']['RAW_0_READY']['name'] = name
                self.rejected(fields)
        fields = deepcopy(base)
        fields['resources']['RAW_0_READY']['name'] = '/' + 's' * 127
        self.assertEqual(len(self.parse(fields).resource('RAW_0_READY').name), 128)

    def test_known_names_are_unique_across_kinds_and_shm_objects_across_prefixes(self):
        fields = full_fields()
        fields['resources']['RAW_0_READY']['name'] = fields['resources']['RAW_CURRENT']['name']
        self.rejected(fields)
        fields = full_fields()
        object_id = fields['resources']['RAW_CURRENT']['object_id']
        fields['resources']['DB_COPY_0']['object_id'] = object_id
        fields['resources']['DB_COPY_0']['name'] = '/xnv_db_' + object_id
        self.rejected(fields)

    def test_pending_resource_and_exactly_one_intent_are_bijective(self):
        fields = pending_fields()
        fields['intents'] = []
        self.rejected(fields)
        fields = pending_fields()
        fields['resources']['RAW_CURRENT'] = dict(state='NOT_ATTEMPTED', name=None, object_id=None, size_bytes=None)
        self.rejected(fields)
        fields = pending_fields()
        fields['resources']['DB_COPY_0'] = dict(state='UNKNOWN', name=None, object_id=None, size_bytes=None)
        self.rejected(fields)
        fields = pending_fields()
        fields['intents'].append(deepcopy(fields['intents'][0]))
        self.rejected(fields)

    def test_intent_target_operation_and_planned_fields_match_resource_kind(self):
        for name, wrong in (('slot', 'DB_COPY_0'), ('slot', 'RAW_RETIRED'), ('operation', 'DELETE'),
                            ('planned_name', '/wrong'), ('planned_object_id', None),
                            ('planned_size_bytes', '0')):
            with self.subTest(name=name):
                fields = pending_fields()
                fields['intents'][0][name] = wrong
                self.rejected(fields)
        for name, wrong in (('planned_name', '/known'), ('planned_object_id', identifier(802)), ('planned_size_bytes', '1')):
            fields = pending_fields(slot='RAW_0_READY')
            fields['intents'][0][name] = wrong
            self.rejected(fields)

    def test_planned_shm_uuid_or_name_cannot_conflict_with_known_inventory(self):
        fields = pending_fields()
        # fixture가 최소 inventory를 쓰더라도 충돌 상대를 명시해 이 조건만 확인한다.
        object_id = identifier(803)
        fields['resources']['DB_COPY_0'] = dict(state='KNOWN', name='/xnv_db_' + object_id,
                                               object_id=object_id, size_bytes='1')
        fields['intents'][0].update(planned_name='/xnv_raw_' + object_id, planned_object_id=object_id)
        self.rejected(fields)
        fields = pending_fields()
        fields['resources']['RAW_0_READY'] = dict(state='KNOWN', name=fields['intents'][0]['planned_name'],
                                                  object_id=None, size_bytes=None)
        self.rejected(fields)

    def test_clean_cannot_hide_pending_intent_unknown_or_unconfirmed_birth(self):
        for state in ('INTENT', 'UNKNOWN'):
            fields = pending_fields(state=state)
            fields['state'] = 'CLEAN'
            self.rejected(fields)
        fields = minimal_fields()
        fields['state'] = 'CLEAN'
        fields['owners'].append(dict(role='M', phase='CURRENT', pid=None, start_ticks=None))
        self.rejected(fields)

    def test_recovery_episode_keeps_all_five_attempts_without_reset(self):
        fields = minimal_fields()
        fields['recovery_episode']['service_attempts'] = 5
        fields['recovery_episode']['role_attempts'] = dict(A=5, M=5, D=5)
        manifest = self.parse(fields)
        self.assertEqual(manifest.recovery_episode.service_attempts, 5)
        self.assertEqual(manifest.recovery_episode.role_attempts, (5, 5, 5))
        self.assertEqual(manifest.fields['recovery_episode'], fields['recovery_episode'])
        for name in ('service_attempts', 'A', 'M', 'D'):
            for wrong in (-1, 6, True, '5'):
                with self.subTest(name=name, wrong=wrong):
                    altered = deepcopy(fields)
                    target = altered['recovery_episode'] if name == 'service_attempts' else altered['recovery_episode']['role_attempts']
                    target[name] = wrong
                    self.rejected(altered)

    def test_fields_and_all_public_records_are_owned_immutable_values(self):
        fields = full_fields()
        raw = encode(fields)
        manifest = records.ResourceManifest(raw)
        before = manifest.fields
        fields['owners'].clear()
        public = manifest.fields
        public['runtime']['boot_id'] = identifier(804)
        public['resources'].clear()
        self.assertEqual(manifest.fields, before)
        self.assertEqual(manifest.raw_bytes, raw)
        self.assertIs(type(manifest.owners), tuple)
        self.assertIs(type(manifest.intents), tuple)
        for obj, name in ((manifest, 'raw_bytes'), (manifest.runtime, 'shm_dev'),
                          (manifest.owners[0], 'pid'), (manifest.cgroup, 'path'),
                          (manifest.resources[0], 'state'), (manifest.recovery_episode, 'service_attempts'),
                          (manifest.ref, 'raw_sha256')):
            with self.subTest(record=type(obj).__name__):
                with self.assertRaises(AttributeError):
                    setattr(obj, name, None)

    def test_public_numeric_records_use_exact_native_ints_not_wire_strings(self):
        for pid, start in ((1, 0), (PID_MAX, MAX)):
            birth = records.ProcessBirth(pid, start)
            self.assertEqual((birth.pid, birth.start_ticks), (pid, start))
        for wrong in (True, False, '1', 1.0, -1, MAX + 1):
            with self.subTest(wrong=wrong):
                with self.assertRaises(records.ManifestError):
                    records.ProcessBirth(1, wrong)
                with self.assertRaises(records.ManifestError):
                    replace(runtime(), shm_mount_id=wrong)
        for wrong in (0, PID_MAX + 1, True, '1'):
            with self.assertRaises(records.ManifestError):
                records.ProcessBirth(wrong, 0)

    def test_public_resource_intent_and_episode_constructors_check_own_invariants(self):
        raw_id = identifier(805)
        valid = records.ResourceRecord('RAW_CURRENT', 'KNOWN', '/xnv_raw_' + raw_id, raw_id, MAX)
        self.assertEqual(valid.size_bytes, MAX)
        self.assertEqual(valid.kind, 'SHM')
        self.assertEqual(records.ResourceRecord('RAW_0_READY', 'KNOWN', '/s', None, None).kind, 'SEMAPHORE')
        for arguments in (('RAW_CURRENT', 'KNOWN', '/wrong', raw_id, 1),
                          ('RAW_0_READY', 'KNOWN', '/s', raw_id, None),
                          ('RAW_CURRENT', 'UNKNOWN', '/s', None, None)):
            with self.assertRaises(records.ManifestError):
                records.ResourceRecord(*arguments)
        for arguments in ((identifier(806), 'RAW_RETIRED', 'CREATE', '/xnv_raw_' + raw_id, raw_id, 1),
                          (identifier(806), 'RAW_0_READY', 'CREATE', '/s', None, None)):
            with self.assertRaises(records.ManifestError):
                records.ResourceIntent(*arguments)
        for counts in ([0, 0, 0], (0, 0), (0, 0, True), (0, 0, 6)):
            with self.assertRaises(records.ManifestError):
                records.RecoveryEpisode(identifier(807), 0, counts)

    def test_manifest_ref_uses_exact_raw_sha_revision_and_runtime_record(self):
        value = self.parse(minimal_fields())
        expected = records.ManifestRef(value.raw_sha256, value.revision, value.service_instance,
                                       value.resource_epoch, value.runtime)
        self.assertEqual(value.ref, expected)
        for change in (dict(raw_sha256='F' * 64), dict(revision=0), dict(revision='1'), dict(runtime={})):
            with self.subTest(change=change):
                with self.assertRaises(records.ManifestError):
                    replace(expected, **change)

    def test_native_custom_types_and_subclasses_are_rejected_without_callbacks(self):
        calls = []

        class TrapMeta(type):
            def __eq__(cls, other):
                calls.append('metaclass equality')
                raise AssertionError('custom equality must not run')

            def __hash__(cls):
                calls.append('metaclass hash')
                raise AssertionError('custom hash must not run')

        class Trap(metaclass=TrapMeta):
            def __getattr__(self, name):
                calls.append('attribute access')
                raise AssertionError('custom attribute must not run')

            def __index__(self):
                calls.append('index conversion')
                raise AssertionError('numeric coercion must not run')

        class DerivedBytes(bytes, metaclass=TrapMeta):
            def __len__(self):
                calls.append('bytes length')
                raise AssertionError('subclass length must not run')

        class DerivedString(str, metaclass=TrapMeta):
            def __len__(self):
                calls.append('string length')
                raise AssertionError('subclass length must not run')

        for value in (Trap(), DerivedBytes(b'{}'), {}, bytearray(b'{}'), memoryview(b'{}')):
            with self.assertRaises(records.ManifestError):
                records.ResourceManifest(value)
        for call in (lambda: records.ProcessBirth(Trap(), 0),
                     lambda: records.CgroupIdentity(DerivedString('SYSTEMD_V1'), '/', 0, 1),
                     lambda: records.OwnerRecord(Trap(), 'CURRENT', None, None),
                     lambda: records.ResourceRecord(Trap(), 'NOT_ATTEMPTED', None, None, None),
                     lambda: records.RecoveryEpisode(identifier(808), 0, Trap())):
            with self.assertRaises(records.ManifestError):
                call()
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()

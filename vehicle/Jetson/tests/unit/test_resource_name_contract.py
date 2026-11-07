"""합성 DTO의 원 source·guard·시각·이름 관계 시험. 실제 OS 관측이나 creator proof가 아니다."""

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from resource_guard_fixtures import full_fields, guard, identifier, manifest, pending_fields
from xenova_vehicle.runtime.process_observation import ObservationBatch, ProcessObserver
from xenova_vehicle.runtime.resource_guard import LoadOutcome, NameObservation, OwnerObservation
from xenova_vehicle.runtime.resource_manifest import ResourceManifest
from xenova_vehicle.runtime.resource_store import ResourceStore, StoreLoad
from xenova_vehicle.runtime.resource_name_observation import (
    NameObservationBatch, NameObservationUsageError,
)


def _batch(source, current, *, ids=(701, 702), times=(100, 200), owner_outcomes=None):
    """공개 값 constructor만 쓰는 합성 fixture다. 실제 load/observer를 호출하지 않는다."""
    kind = "MISSING" if source is None else "VALID"
    original = StoreLoad(current.guard_id, LoadOutcome(identifier(ids[0]), kind, source))
    latest = StoreLoad(current.guard_id, LoadOutcome(identifier(ids[1]), kind, source))
    known = () if source is None else source.known_owners
    outcomes = ("UNKNOWN",) * len(known) if owner_outcomes is None else owner_outcomes
    owners = tuple(OwnerObservation(source.ref, current, owner, outcome) for owner, outcome in zip(known, outcomes))
    return ObservationBatch(original, latest, current, owners, times[0], times[1])


def _pair(source=None):
    source = manifest(full_fields()) if source is None else source
    current = guard(source)
    return (_batch(source, current),
            _batch(source, replace(current), ids=(703, 704), times=(300, 400)))


def _names(before, outcomes=None):
    source = before.original_load.outcome.manifest
    records = source.known_resources
    outcomes = ("UNKNOWN",) * len(records) if outcomes is None else outcomes
    return tuple(NameObservation(source.ref, before.guard, record, outcome) for record, outcome in zip(records, outcomes))


class ResourceNameContractTests(unittest.TestCase):
    def test_constructed_value_preserves_two_original_batches_all_original_properties_and_tuple(self):
        before, after = _pair()
        names = _names(before)
        result = NameObservationBatch(before, after, names)
        self.assertIs(result.before, before)
        self.assertIs(result.after, after)
        self.assertIs(result.names, names)
        self.assertIs(result.original_load, before.original_load)
        self.assertIs(result.latest_load, after.latest_load)
        self.assertIs(result.guard, before.guard)
        self.assertEqual((result.started_ns, result.completed_ns), (100, 400))
        self.assertEqual(len({load.outcome.load_id for load in (
            before.original_load, before.latest_load, after.original_load, after.latest_load)}), 4)

    def test_distinct_equal_guard_objects_and_separately_parsed_equal_raw_are_legal_values(self):
        before, after = _pair()
        original = before.original_load.outcome.manifest
        clone = ResourceManifest(original.raw_bytes)
        after = _batch(clone, replace(before.guard), ids=(703, 704), times=(300, 400))
        self.assertIsNot(before.guard, after.guard)
        self.assertEqual(before.guard, after.guard)
        self.assertIsNot(original, clone)
        result = NameObservationBatch(before, after, _names(before))
        self.assertIs(result.guard, before.guard)
        self.assertIs(result.after, after)

    def test_owner_outcomes_may_change_and_two_original_owner_tuples_are_not_flattened(self):
        before, after = _pair()
        source = before.original_load.outcome.manifest
        before = _batch(source, before.guard, owner_outcomes=("ALIVE",) * len(source.known_owners))
        after = _batch(source, after.guard, ids=(703, 704), times=(300, 400),
                       owner_outcomes=("EXITED",) * len(source.known_owners))
        result = NameObservationBatch(before, after, _names(before))
        self.assertIs(result.before.owners, before.owners)
        self.assertIs(result.after.owners, after.owners)
        self.assertEqual({item.outcome for item in result.before.owners}, {"ALIVE"})
        self.assertEqual({item.outcome for item in result.after.owners}, {"EXITED"})

    def test_valid_zero_known_keeps_incomplete_source_and_all_four_loads(self):
        fields = pending_fields(state="UNKNOWN")
        for slot in fields["resources"]:
            if slot != "RAW_CURRENT":
                fields["resources"][slot] = dict(state="NOT_ATTEMPTED", name=None, object_id=None, size_bytes=None)
        source = manifest(fields)
        self.assertEqual(source.known_resources, ())
        self.assertFalse(source.complete_inventory)
        before, after = _pair(source)
        result = NameObservationBatch(before, after, ())
        self.assertEqual(result.names, ())
        self.assertIs(result.original_load.outcome.manifest, source)
        self.assertEqual(result.original_load.outcome.manifest.raw_bytes, source.raw_bytes)
        self.assertIs(result.latest_load, after.latest_load)

    def test_known_twelve_and_unknown_rows_are_retained_without_extra_authority(self):
        before, after = _pair()
        outcomes = ("ABSENT", "PRESENT", "UNKNOWN") * 4
        names = _names(before, outcomes)
        self.assertEqual(len(names), 12)
        result = NameObservationBatch(before, after, names)
        self.assertEqual(tuple(item.outcome for item in result.names), outcomes)
        self.assertEqual(tuple(item.resource for item in result.names), before.original_load.outcome.manifest.known_resources)
        for name in ("clean", "creator_verified", "native_abi_verified", "ready", "creation_allowed"):
            self.assertFalse(hasattr(result, name))

    def test_partial_inventory_covers_only_original_known_resources(self):
        source = manifest(pending_fields(state="INTENT"))
        before, after = _pair(source)
        names = _names(before)
        self.assertEqual(len(names), 11)
        self.assertFalse(source.complete_inventory)
        result = NameObservationBatch(before, after, names)
        self.assertEqual(tuple(item.resource for item in result.names), source.known_resources)
        self.assertEqual(result.original_load.outcome.manifest.intents, source.intents)

    def test_every_cross_batch_load_id_collision_is_rejected_even_with_separate_valid_pairs(self):
        before, _ = _pair()
        source = before.original_load.outcome.manifest
        for before_id in (701, 702):
            for position in (0, 1):
                ids = [703, 704]
                ids[position] = before_id
                after = _batch(source, replace(before.guard), ids=tuple(ids), times=(300, 400))
                with self.subTest(before_id=before_id, after_position=position), self.assertRaises(NameObservationUsageError):
                    NameObservationBatch(before, after, _names(before))

    def test_same_canonical_but_different_raw_and_ref_across_batches_is_rejected(self):
        before, _ = _pair()
        source = before.original_load.outcome.manifest
        alternate = ResourceManifest((json.dumps(source.fields, indent=2) + "\n").encode("ascii"))
        self.assertEqual(source.canonical_bytes, alternate.canonical_bytes)
        self.assertNotEqual(source.raw_bytes, alternate.raw_bytes)
        after = _batch(alternate, replace(before.guard), ids=(703, 704), times=(300, 400))
        with self.assertRaises(NameObservationUsageError):
            NameObservationBatch(before, after, _names(before))

    def test_revision_epoch_or_manifest_service_change_cannot_retarget_original_names(self):
        before, _ = _pair()
        source = before.original_load.outcome.manifest
        for key, value in (("revision", "2"), ("resource_epoch", identifier(801)), ("service_instance", identifier(802))):
            fields = source.fields
            fields[key] = value
            after = _batch(manifest(fields), replace(before.guard), ids=(703, 704), times=(300, 400))
            with self.subTest(key=key), self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, _names(before))

    def test_full_guard_comparison_includes_nonce_service_and_both_birth_fields(self):
        before, _ = _pair()
        source = before.original_load.outcome.manifest
        original = before.guard
        variants = (replace(original, guard_id=identifier(803)),
                    replace(original, service_instance=identifier(804)),
                    replace(original, birth=replace(original.birth, pid=original.birth.pid + 1)),
                    replace(original, birth=replace(original.birth, start_ticks=original.birth.start_ticks + 1)))
        for current in variants:
            after = _batch(source, current, ids=(703, 704), times=(300, 400))
            with self.subTest(current=current), self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, _names(before))

    def test_cross_batch_guard_runtime_comparison_includes_all_six_variable_fields(self):
        before, _ = _pair()
        source = before.original_load.outcome.manifest
        for key in ("boot_id", "pid_ns_inode", "mnt_ns_inode", "shm_mount_id", "shm_dev", "shm_root_inode"):
            value = identifier(805) if key == "boot_id" else getattr(before.guard.runtime, key) + 1
            current = replace(before.guard, runtime=replace(before.guard.runtime, **{key: value}))
            after = _batch(source, current, ids=(703, 704), times=(300, 400))
            with self.subTest(key=key), self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, _names(before))

    def test_cross_batch_clock_touch_and_extreme_original_brackets_are_legal(self):
        source = manifest(full_fields())
        current = guard(source)
        for first, second in (((0, 0), (0, 2**64 - 1)), ((100, 200), (200, 400))):
            before = _batch(source, current, times=first)
            after = _batch(source, replace(current), ids=(703, 704), times=second)
            result = NameObservationBatch(before, after, _names(before))
            self.assertEqual((result.started_ns, result.completed_ns), (first[0], second[1]))

    def test_individually_valid_brackets_cannot_overlap_or_regress_between_batches(self):
        before, _ = _pair()
        source = before.original_load.outcome.manifest
        for times in ((199, 400), (0, 99), (100, 200)):
            after = _batch(source, replace(before.guard), ids=(703, 704), times=times)
            with self.subTest(times=times), self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, _names(before))

    def test_missing_endpoint_cannot_become_empty_names_success(self):
        before, after = _pair()
        missing_before = _batch(None, before.guard)
        missing_after = _batch(None, after.guard, ids=(703, 704), times=(300, 400))
        for first, second, names in ((missing_before, after, ()), (before, missing_after, _names(before)),
                                     (missing_before, missing_after, ())):
            with self.subTest(first=first.original_load.outcome.kind, second=second.original_load.outcome.kind), \
                    self.assertRaises(NameObservationUsageError):
                NameObservationBatch(first, second, names)

    def test_source_runtime_mismatch_keeps_all_unknown_for_six_representable_variants(self):
        source = manifest(full_fields())
        for key in ("boot_id", "pid_ns_inode", "mnt_ns_inode", "shm_mount_id", "shm_dev", "shm_root_inode"):
            value = identifier(806) if key == "boot_id" else getattr(source.runtime, key) + 1
            current = replace(guard(source), runtime=replace(source.runtime, **{key: value}))
            before = _batch(source, current)
            after = _batch(source, replace(current), ids=(703, 704), times=(300, 400))
            names = _names(before)
            result = NameObservationBatch(before, after, names)
            self.assertEqual(tuple(item.outcome for item in result.names), ("UNKNOWN",) * 12)
            self.assertEqual(result.original_load.outcome.manifest.runtime, source.runtime)
            for invalid in ("ABSENT", "PRESENT"):
                changed = (replace(names[0], outcome=invalid),) + names[1:]
                with self.subTest(key=key, outcome=invalid), self.assertRaises(NameObservationUsageError):
                    NameObservationBatch(before, after, changed)

    def test_original_resource_coverage_missing_duplicate_reordered_or_thirteen_is_rejected(self):
        before, after = _pair()
        names = _names(before)
        for value in ((), names[:-1], names[:-1] + (names[0],), tuple(reversed(names)), names + (names[0],)):
            with self.subTest(length=len(value)), self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, value)

    def test_changed_full_resource_record_cannot_substitute_for_original_target(self):
        before, after = _pair()
        names = _names(before)
        original = names[0].resource
        identity = identifier(807)
        variants = (replace(original, size_bytes=original.size_bytes + 1),
                    replace(original, name="/xnv_raw_" + identity, object_id=identity))
        for resource in variants:
            altered = (replace(names[0], resource=resource),) + names[1:]
            with self.subTest(resource=resource), self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, altered)

    def test_name_ref_or_guard_mismatch_cannot_be_hidden_by_correct_coverage(self):
        before, after = _pair()
        names = _names(before)
        ref = before.original_load.outcome.manifest.ref
        variants = (replace(names[0], ref=replace(ref, raw_sha256="f" * 64)),
                    replace(names[0], ref=replace(ref, revision=ref.revision + 1)),
                    replace(names[0], ref=replace(ref, service_instance=identifier(808))),
                    replace(names[0], ref=replace(ref, resource_epoch=identifier(809))),
                    replace(names[0], ref=replace(ref, runtime=replace(ref.runtime, shm_dev=ref.runtime.shm_dev + 1))),
                    replace(names[0], guard=replace(before.guard, service_instance=identifier(810))),
                    replace(names[0], guard=replace(before.guard, guard_id=identifier(811))),
                    replace(names[0], guard=replace(before.guard, birth=replace(before.guard.birth, start_ticks=1))),
                    replace(names[0], guard=replace(before.guard, runtime=replace(before.guard.runtime, shm_mount_id=99))))
        for altered in variants:
            with self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, (altered,) + names[1:])

    def test_result_fields_and_original_properties_are_frozen_and_have_no_override_arguments(self):
        before, after = _pair()
        names = _names(before)
        result = NameObservationBatch(before, after, names)
        for key, value in (("before", after), ("after", before), ("names", ()),
                           ("original_load", after.latest_load), ("latest_load", before.latest_load),
                           ("guard", after.guard), ("started_ns", 0), ("completed_ns", 0)):
            with self.subTest(key=key), self.assertRaises(AttributeError):
                setattr(result, key, value)
        for key in ("proof", "validated", "original_load", "latest_load", "guard", "started_ns", "completed_ns"):
            with self.subTest(override=key), self.assertRaises(TypeError):
                NameObservationBatch(before, after, names, **{key: True})

    def test_value_constructor_never_calls_native_observer_store_load_or_open(self):
        before, after = _pair()
        with patch.object(ProcessObserver, "observe", side_effect=AssertionError("pure DTO performed observe")), \
                patch.object(ResourceStore, "load", side_effect=AssertionError("pure DTO performed load")), \
                patch("os.open", side_effect=AssertionError("pure DTO opened a file")):
            result = NameObservationBatch(before, after, _names(before))
            self.assertIs(result.latest_load, after.latest_load)
            self.assertIs(result.original_load, before.original_load)

    def test_exact_batch_and_name_container_types_reject_bool_or_mutable_inputs(self):
        before, after = _pair()
        names = _names(before)
        for first, second in ((True, after), (before, False), (None, after), (before.original_load, after)):
            with self.assertRaises(NameObservationUsageError):
                NameObservationBatch(first, second, names)
        for value in (True, None, list(names), {}, (True,) + names[1:]):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(NameObservationUsageError):
                NameObservationBatch(before, after, value)

    def test_exact_native_guards_do_not_invoke_metaclass_or_conversion_hooks(self):
        before, after = _pair()
        names = _names(before)
        calls = []

        class TrapMeta(type):
            def __eq__(self, other):
                calls.append("type.eq")
                raise AssertionError("native type guard invoked metaclass equality")

        class Trap(metaclass=TrapMeta):
            def __iter__(self):
                calls.append("iter")
                raise AssertionError("native guard invoked conversion hook")

        trap = Trap()
        for first, second, value in ((trap, after, names), (before, trap, names),
                                     (before, after, trap), (before, after, (trap,) + names[1:]),
                                     (before, after, (trap,) * 13)):
            with self.assertRaises(NameObservationUsageError):
                NameObservationBatch(first, second, value)
        self.assertEqual(calls, [])

    def test_subclasses_cannot_replace_exact_original_batch_name_record_or_tuple_types(self):
        before, after = _pair()
        names = _names(before)

        class BatchSubclass(ObservationBatch):
            pass

        class NameSubclass(NameObservation):
            pass

        class TupleSubclass(tuple):
            pass

        derived = BatchSubclass(before.original_load, before.latest_load, before.guard, before.owners,
                                before.started_ns, before.completed_ns)
        item = NameSubclass(names[0].ref, names[0].guard, names[0].resource, names[0].outcome)
        variants = ((derived, after, names), (before, after, (item,) + names[1:]),
                    (before, after, TupleSubclass(names)))
        for first, second, value in variants:
            with self.assertRaises(NameObservationUsageError):
                NameObservationBatch(first, second, value)


if __name__ == "__main__":
    unittest.main()

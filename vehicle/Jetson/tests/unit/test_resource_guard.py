"""합성 manifest·관측 진술의 순수 guard 판정. 실제 OS/설치/reset/내구 ACK 증명은 아니다."""

from dataclasses import FrozenInstanceError, fields, replace
import json
import unittest

from xenova_vehicle.runtime.resource_manifest import (
    ProcessBirth, RecoveryEpisode, ResourceManifest,
)
from xenova_vehicle.runtime.resource_guard import (
    CgroupObservation, CurrentGuardScope, FirstInstallStatement, GuardDecision,
    GuardEvidence, GuardInputError, LiveIntentStatement, LoadOutcome, NameObservation,
    OwnerObservation, RuntimeResetStatement, evaluate_guard,
)


_SLOTS = ("RAW_CURRENT", "RAW_RETIRED", "DB_COPY_0", "DB_COPY_1", "DB_COPY_2", "DB_COPY_3",
          "RAW_0_WRITABLE", "RAW_0_READY", "RAW_1_WRITABLE", "RAW_1_READY", "DB_WRITABLE", "DB_READY")


def _id(number):
    return "00000000-0000-4000-8000-{:012x}".format(number)


def _minimal_fields():
    """문서 exact grammar의 literal이다. 제품 판정으로 기대값을 만들지 않는다."""
    return dict(version=1,
                runtime=dict(boot_id=_id(1), pid_ns_inode="11", mnt_ns_inode="22", shm_mount_id="33",
                             shm_dev="44", shm_root_inode="55", shm_fs="tmpfs"),
                service_instance=_id(2), resource_epoch=_id(3), revision="1", state="DIRTY",
                owners=[dict(role="S", phase="CURRENT", pid=100, start_ticks="1000")],
                cgroup=dict(mode="SYSTEMD_V1", path="/system.slice/xenova-vehicle.service", device="8", inode="2000"),
                resources={slot: dict(state="NOT_ATTEMPTED", name=None, object_id=None, size_bytes=None)
                           for slot in _SLOTS}, intents=[],
                recovery_episode=dict(episode_id=_id(4), service_attempts=0, role_attempts=dict(A=0, M=0, D=0)))


def _manifest(value=None):
    value = _minimal_fields() if value is None else value
    return ResourceManifest((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"))


def _full_fields():
    result = _minimal_fields()
    for index, role in enumerate(("A", "M", "D", "TRACKER")):
        result["owners"].append(dict(role=role, phase="CURRENT", pid=200 + index, start_ticks=str(2000 + index)))
        result["owners"].append(dict(role=role, phase="RETIRED", pid=300 + index, start_ticks=str(3000 + index)))
    for index, slot in enumerate(_SLOTS):
        if slot.startswith(("RAW_CURRENT", "RAW_RETIRED", "DB_COPY_")):
            identity = _id(100 + index)
            prefix = "/xnv_raw_" if slot.startswith("RAW_") else "/xnv_db_"
            row = dict(state="KNOWN", name=prefix + identity, object_id=identity, size_bytes=str(4194304 + index))
        else:
            row = dict(state="KNOWN", name="/guard_test_" + slot, object_id=None, size_bytes=None)
        result["resources"][slot] = row
    return result


def _pending_fields(*, state="INTENT", full=False):
    result = _full_fields() if full else _minimal_fields()
    result["resources"]["RAW_0_READY"] = dict(state=state, name=None, object_id=None, size_bytes=None)
    result["intents"] = [dict(intent_id=_id(400), slot="RAW_0_READY", operation="CREATE",
                              planned_name=None, planned_object_id=None, planned_size_bytes=None)]
    return result


def _guard(manifest, *, live=False):
    birth = next(owner.birth for owner in manifest.owners if owner.role == "S") if live else ProcessBirth(9000, 90000)
    service = manifest.service_instance if live else _id(901)
    return CurrentGuardScope(_id(900), service, manifest.runtime, birth)


def _load(manifest=None, *, kind="VALID"):
    return LoadOutcome(_id(902), kind, manifest)


def _old_evidence(manifest, guard):
    return GuardEvidence(
        owners=tuple(OwnerObservation(manifest.ref, guard, owner, "EXITED") for owner in manifest.known_owners),
        cgroup=CgroupObservation(manifest.ref, guard, manifest.cgroup, "COMPLETE", (guard.birth,)),
        names=tuple(NameObservation(manifest.ref, guard, resource, "ABSENT") for resource in manifest.known_resources),
    )


def _reset(manifest, guard):
    return RuntimeResetStatement(manifest.ref, guard, _id(910), "a" * 64)


def _install(load, guard, *, genesis=None):
    genesis = RecoveryEpisode(_id(911), 5, (5, 4, 3)) if genesis is None else genesis
    return FirstInstallStatement(load.load_id, guard, _id(912), "b" * 64, genesis)


class ResourceGuardTests(unittest.TestCase):
    def assert_decision(self, expected, load, guard, evidence):
        result = None
        for entry in (evaluate_guard, GuardDecision):
            with self.subTest(entry=entry.__name__):
                result = entry(load, guard, evidence)
                self.assertEqual(result.kind, expected)
                if expected == "BLOCKED":
                    self.assertTrue(result.reason)
                else:
                    self.assertIsNone(result.reason)
                self.assertIs(result.load, load)
                self.assertIs(result.guard, guard)
                self.assertIs(result.evidence, evidence)
                if load.kind == "VALID":
                    self.assertIs(result.predecessor, load.manifest)
                    self.assertEqual(result.episode, load.manifest.recovery_episode)
                else:
                    self.assertIsNone(result.predecessor)
        return result

    def test_old_minimal_not_attempted_slots_can_be_complete_without_creating_six_tokens(self):
        manifest = _manifest()
        guard = _guard(manifest)
        self.assertTrue(manifest.complete_inventory)
        self.assertEqual(manifest.known_resources, ())
        result = self.assert_decision("OLD_CLEAN_ELIGIBLE", _load(manifest), guard, _old_evidence(manifest, guard))
        self.assertEqual(result.predecessor.raw_bytes, manifest.raw_bytes)
        self.assertFalse(any(hasattr(result, name) for name in (
            "clean_manifest", "create_allowed", "actual_exit", "resources_absent", "lock_acquired", "ready")))

    def test_old_full_inventory_requires_observations_even_when_state_says_clean(self):
        for state in ("DIRTY", "CLEANUP_CANDIDATE", "CLEAN"):
            fields = _full_fields()
            fields["state"] = state
            manifest = _manifest(fields)
            guard = _guard(manifest)
            evidence = _old_evidence(manifest, guard)
            self.assertEqual((len(evidence.owners), len(evidence.names)), (9, 12))
            self.assert_decision("BLOCKED", _load(manifest), guard, GuardEvidence())
            self.assert_decision("OLD_CLEAN_ELIGIBLE", _load(manifest), guard, evidence)
            self.assertEqual(manifest.state, state)

    def test_old_missing_duplicate_wrong_owner_or_name_coverage_is_blocked(self):
        manifest = _manifest(_full_fields())
        guard = _guard(manifest)
        evidence = _old_evidence(manifest, guard)
        variants = (replace(evidence, owners=evidence.owners[:-1]), replace(evidence, names=evidence.names[:-1]),
                    replace(evidence, owners=evidence.owners[:-1] + (evidence.owners[0],)),
                    replace(evidence, names=evidence.names[:-1] + (evidence.names[0],)),
                    replace(evidence, cgroup=None))
        for index, candidate in enumerate(variants):
            with self.subTest(index=index):
                self.assert_decision("BLOCKED", _load(manifest), guard, candidate)

    def test_old_alive_unknown_present_and_unqueried_members_are_not_empty_evidence(self):
        manifest = _manifest(_full_fields())
        guard = _guard(manifest)
        evidence = _old_evidence(manifest, guard)
        for outcome in ("ALIVE", "UNKNOWN"):
            candidate = replace(evidence, owners=(replace(evidence.owners[0], outcome=outcome),) + evidence.owners[1:])
            self.assert_decision("BLOCKED", _load(manifest), guard, candidate)
        for outcome in ("PRESENT", "UNKNOWN"):
            candidate = replace(evidence, names=(replace(evidence.names[0], outcome=outcome),) + evidence.names[1:])
            self.assert_decision("BLOCKED", _load(manifest), guard, candidate)
        self.assert_decision("BLOCKED", _load(manifest), guard,
                             replace(evidence, cgroup=replace(evidence.cgroup, outcome="UNKNOWN")))
        extra = ProcessBirth(guard.birth.pid, guard.birth.start_ticks + 1)
        self.assert_decision("BLOCKED", _load(manifest), guard,
                             replace(evidence, cgroup=replace(evidence.cgroup, members=(guard.birth, extra))))
        self.assert_decision("OLD_CLEAN_ELIGIBLE", _load(manifest), guard,
                             replace(evidence, cgroup=replace(evidence.cgroup, members=())))

    def test_full_refs_current_guard_nonce_and_exact_targets_are_not_approximated(self):
        manifest = _manifest(_full_fields())
        guard = _guard(manifest)
        evidence = _old_evidence(manifest, guard)
        refs = (replace(manifest.ref, raw_sha256="f" * 64), replace(manifest.ref, revision=2),
                replace(manifest.ref, service_instance=_id(920)), replace(manifest.ref, resource_epoch=_id(921)),
                replace(manifest.ref, runtime=replace(manifest.runtime, shm_root_inode=56)))
        for ref in refs:
            for field in ("owners", "names", "cgroup"):
                if field == "cgroup":
                    candidate = replace(evidence, cgroup=replace(evidence.cgroup, ref=ref))
                else:
                    original = getattr(evidence, field)
                    candidate = replace(evidence, **{field: (replace(original[0], ref=ref),) + original[1:]})
                self.assert_decision("BLOCKED", _load(manifest), guard, candidate)
        wrong_guard = replace(guard, guard_id=_id(922))
        self.assert_decision("BLOCKED", _load(manifest), guard,
                             replace(evidence, cgroup=replace(evidence.cgroup, guard=wrong_guard)))
        for changed in (replace(manifest.cgroup, inode=manifest.cgroup.inode + 1),
                        replace(manifest.cgroup, device=manifest.cgroup.device + 1),
                        replace(manifest.cgroup, path=manifest.cgroup.path + "-other")):
            self.assert_decision("BLOCKED", _load(manifest), guard,
                                 replace(evidence, cgroup=replace(evidence.cgroup, cgroup=changed)))

    def test_same_pid_different_start_or_owner_phase_cannot_substitute_recorded_owner(self):
        manifest = _manifest(_full_fields())
        guard = _guard(manifest)
        evidence = _old_evidence(manifest, guard)
        owner = evidence.owners[0]
        reused = replace(owner.owner, start_ticks=owner.owner.start_ticks + 1)
        candidate = replace(evidence, owners=(replace(owner, owner=reused),) + evidence.owners[1:])
        self.assert_decision("BLOCKED", _load(manifest), guard, candidate)
        selected = next(i for i, observation in enumerate(evidence.owners) if observation.owner.role == "A")
        other = replace(evidence.owners[selected].owner, pid=9999)
        altered = list(evidence.owners)
        altered[selected] = replace(altered[selected], owner=other)
        self.assert_decision("BLOCKED", _load(manifest), guard, replace(evidence, owners=tuple(altered)))

    def test_current_guard_cannot_claim_its_own_exit_by_changing_only_service_uuid(self):
        manifest = _manifest()
        old_s = manifest.known_owners[0]
        guard = replace(_guard(manifest), birth=old_s.birth, service_instance=_id(999))
        self.assert_decision("BLOCKED", _load(manifest), guard, _old_evidence(manifest, guard))
        # mount만 달라져도 boot/PID namespace/PID/start가 같으면 자기 process다.
        changed = replace(guard, runtime=replace(guard.runtime, mnt_ns_inode=guard.runtime.mnt_ns_inode + 1))
        self.assert_decision("BLOCKED", _load(manifest), changed, _old_evidence(manifest, changed))

    def test_incomplete_intent_unknown_or_pending_birth_cannot_be_old_clean(self):
        candidates = [_pending_fields(state="INTENT"), _pending_fields(state="UNKNOWN")]
        pending_owner = _minimal_fields()
        pending_owner["owners"].append(dict(role="A", phase="CURRENT", pid=None, start_ticks=None))
        candidates.append(pending_owner)
        for fields in candidates:
            manifest = _manifest(fields)
            guard = _guard(manifest)
            self.assertFalse(manifest.complete_inventory)
            self.assert_decision("BLOCKED", _load(manifest), guard, _old_evidence(manifest, guard))

    def test_live_intent_requires_own_dirty_s_alive_and_only_branch_specific_evidence(self):
        manifest = _manifest(_pending_fields(full=True))
        guard = _guard(manifest, live=True)
        supervisor = next(owner for owner in manifest.known_owners if owner.role == "S")
        live = LiveIntentStatement(manifest.ref, guard, manifest.intents[0].intent_id)
        evidence = GuardEvidence(owners=(OwnerObservation(manifest.ref, guard, supervisor, "ALIVE"),), live=live)
        self.assert_decision("LIVE_INTENT_PENDING", _load(manifest), guard, evidence)
        # 같은 S가 이미 가진 정상 resource/worker의 PRESENT/ALIVE는 OLD의 차단 조건과 다르다.
        extra = GuardEvidence(owners=tuple(OwnerObservation(manifest.ref, guard, o, "ALIVE") for o in manifest.known_owners),
                              names=tuple(NameObservation(manifest.ref, guard, r, "PRESENT") for r in manifest.known_resources),
                              cgroup=CgroupObservation(manifest.ref, guard, manifest.cgroup, "COMPLETE", tuple(o.birth for o in manifest.known_owners)),
                              live=live)
        self.assert_decision("LIVE_INTENT_PENDING", _load(manifest), guard, extra)
        for missing_or_wrong in (replace(evidence, owners=()), replace(evidence, live=None),
                                 replace(evidence, owners=(replace(evidence.owners[0], outcome="UNKNOWN"),)),
                                 replace(evidence, owners=(replace(evidence.owners[0], outcome="EXITED"),)),
                                 replace(evidence, live=replace(live, intent_id=_id(999)))):
            self.assert_decision("BLOCKED", _load(manifest), guard, missing_or_wrong)

    def test_live_unknown_and_other_s_birth_service_runtime_or_guard_cannot_resume(self):
        for state in ("INTENT", "UNKNOWN"):
            manifest = _manifest(_pending_fields(state=state))
            own = _guard(manifest, live=True)
            supervisor = manifest.known_owners[0]
            candidates = (own,) if state == "UNKNOWN" else (
                replace(own, service_instance=_id(930)), replace(own, birth=ProcessBirth(101, 1001)),
                replace(own, runtime=replace(own.runtime, shm_mount_id=own.runtime.shm_mount_id + 1)),
            )
            for guard in candidates:
                evidence = GuardEvidence(owners=(OwnerObservation(manifest.ref, guard, supervisor, "ALIVE"),),
                                         live=LiveIntentStatement(manifest.ref, guard, manifest.intents[0].intent_id))
                self.assert_decision("BLOCKED", _load(manifest), guard, evidence)

    def test_fresh_reset_accepts_incomplete_predecessor_and_preserves_episode_five(self):
        for state in ("INTENT", "UNKNOWN"):
            fields = _pending_fields(state=state)
            fields["recovery_episode"].update(service_attempts=5, role_attempts=dict(A=5, M=5, D=5))
            fields["owners"].append(dict(role="TRACKER", phase="CURRENT", pid=None, start_ticks=None))
            manifest = _manifest(fields)
            guard = replace(_guard(manifest), runtime=replace(manifest.runtime, boot_id=_id(940)))
            result = self.assert_decision("FRESH_RESET_ELIGIBLE", _load(manifest), guard,
                                          GuardEvidence(reset=_reset(manifest, guard)))
            self.assertFalse(result.predecessor.complete_inventory)
            self.assertEqual(result.episode, RecoveryEpisode(_id(4), 5, (5, 5, 5)))
            self.assertEqual(result.predecessor.raw_bytes, manifest.raw_bytes)

    def test_changed_boot_or_mount_numbers_without_exact_reset_statement_are_not_proof(self):
        manifest = _manifest(_pending_fields(state="UNKNOWN"))
        same = _guard(manifest)
        boot = replace(same, runtime=replace(same.runtime, boot_id=_id(941)))
        mount = replace(same, runtime=replace(same.runtime, shm_mount_id=same.runtime.shm_mount_id + 1))
        for guard in (boot, mount):
            self.assert_decision("BLOCKED", _load(manifest), guard, GuardEvidence())
        for guard in (same, mount):
            self.assert_decision("BLOCKED", _load(manifest), guard, GuardEvidence(reset=_reset(manifest, guard)))
        wrong = replace(_reset(manifest, boot), ref=replace(manifest.ref, raw_sha256="e" * 64))
        self.assert_decision("BLOCKED", _load(manifest), boot, GuardEvidence(reset=wrong))

    def test_first_install_uses_only_missing_load_guard_proof_and_explicit_genesis(self):
        old = _manifest()
        guard = _guard(old)
        load = _load(kind="MISSING")
        install = _install(load, guard)
        result = self.assert_decision("FIRST_INSTALL_ELIGIBLE", load, guard, GuardEvidence(install=install))
        self.assertIs(result.episode, install.genesis)
        self.assertEqual(result.episode.service_attempts, 5)
        self.assert_decision("BLOCKED", load, guard, GuardEvidence())
        for candidate in (replace(install, load_id=_id(942)),
                          replace(install, guard=replace(guard, guard_id=_id(943)))):
            self.assert_decision("BLOCKED", load, guard, GuardEvidence(install=candidate))
        for kind in ("CORRUPT", "IO_ERROR"):
            failed = _load(kind=kind)
            result = self.assert_decision("BLOCKED", failed, guard, GuardEvidence(install=_install(failed, guard)))
            self.assertIsNone(result.episode)

    def test_provided_wrong_scope_claims_cannot_be_ignored_by_other_successful_branch(self):
        manifest = _manifest(_full_fields())
        fresh_guard = replace(_guard(manifest), runtime=replace(manifest.runtime, boot_id=_id(944)))
        reset = _reset(manifest, fresh_guard)
        wrong_owner = OwnerObservation(replace(manifest.ref, revision=2), fresh_guard, manifest.known_owners[0], "EXITED")
        self.assert_decision("BLOCKED", _load(manifest), fresh_guard,
                             GuardEvidence(reset=reset, owners=(wrong_owner,)))
        wrong_name = NameObservation(manifest.ref, replace(fresh_guard, guard_id=_id(945)), manifest.known_resources[0], "ABSENT")
        self.assert_decision("BLOCKED", _load(manifest), fresh_guard,
                             GuardEvidence(reset=reset, names=(wrong_name,)))
        missing = _load(kind="MISSING")
        install = _install(missing, fresh_guard)
        self.assert_decision("BLOCKED", missing, fresh_guard,
                             GuardEvidence(install=install, owners=(OwnerObservation(manifest.ref, fresh_guard, manifest.known_owners[0], "EXITED"),)))
        self.assert_decision("BLOCKED", missing, fresh_guard, GuardEvidence(install=install, reset=reset))

    def test_raw_manifest_ref_is_not_replaced_by_equal_canonical_sha(self):
        source = json.dumps(_minimal_fields(), indent=2).encode("ascii")
        manifest = ResourceManifest(source)
        self.assertNotEqual(manifest.raw_sha256, manifest.canonical_sha256)
        guard = _guard(manifest)
        evidence = _old_evidence(manifest, guard)
        self.assert_decision("OLD_CLEAN_ELIGIBLE", _load(manifest), guard, evidence)
        wrong = replace(manifest.ref, raw_sha256=manifest.canonical_sha256)
        self.assert_decision("BLOCKED", _load(manifest), guard,
                             replace(evidence, owners=(replace(evidence.owners[0], ref=wrong),)))

    def test_cgroup_sixteen_native_members_is_not_manifest_list_nine_limit(self):
        manifest = _manifest()
        guard = _guard(manifest)
        members = tuple(ProcessBirth(10000 + index, 20000 + index) for index in range(16))
        observation = CgroupObservation(manifest.ref, guard, manifest.cgroup, "COMPLETE", members)
        self.assertEqual(len(observation.members), 16)
        self.assert_decision("BLOCKED", _load(manifest), guard,
                             replace(_old_evidence(manifest, guard), cgroup=observation))
        for invalid in (members + (ProcessBirth(11000, 21000),), list(members), (members[0], members[0])):
            with self.assertRaises(GuardInputError):
                CgroupObservation(manifest.ref, guard, manifest.cgroup, "COMPLETE", invalid)

    def test_load_shape_and_evidence_container_types_are_input_errors_not_clean_defaults(self):
        manifest = _manifest()
        guard = _guard(manifest)
        evidence = _old_evidence(manifest, guard)
        for kind, value in (("VALID", None), ("MISSING", manifest), ("CORRUPT", manifest), ("IO_ERROR", manifest)):
            with self.assertRaises(GuardInputError):
                LoadOutcome(_id(950), kind, value)
        for kind in (None, True, "valid", "", "CLEAN"):
            with self.assertRaises(GuardInputError):
                LoadOutcome(_id(950), kind)
        for bad in (list(evidence.owners), None, {}, (manifest.known_owners[0],)):
            with self.assertRaises(GuardInputError):
                GuardEvidence(owners=bad)
        for bad in (list(evidence.names), None, {}, (manifest.resource("RAW_CURRENT"),)):
            with self.assertRaises(GuardInputError):
                GuardEvidence(names=bad)

    def test_public_guard_records_and_decision_are_frozen_and_keep_original_refs(self):
        manifest = _manifest(_full_fields())
        guard = _guard(manifest)
        load = _load(manifest)
        evidence = _old_evidence(manifest, guard)
        decision = evaluate_guard(load, guard, evidence)
        pending = _manifest(_pending_fields())
        live_guard = _guard(pending, live=True)
        live = LiveIntentStatement(pending.ref, live_guard, pending.intents[0].intent_id)
        install = _install(_load(kind="MISSING"), guard)
        records = (load, guard, evidence, evidence.owners[0], evidence.cgroup, evidence.names[0],
                   live, _reset(manifest, guard), install, decision)
        for record in records:
            with self.subTest(record=type(record).__name__), self.assertRaises(FrozenInstanceError):
                setattr(record, fields(record)[0].name, None)
        for entry in (evaluate_guard, GuardDecision):
            for name in ("kind", "validated", "_validated", "predecessor"):
                with self.assertRaises(TypeError):
                    entry(load, guard, evidence, **{name: "OLD_CLEAN_ELIGIBLE"})
        exported = manifest.fields
        exported["resources"]["RAW_CURRENT"]["name"] = "/changed"
        self.assertIs(decision.predecessor, manifest)
        self.assertEqual(evaluate_guard(load, guard, evidence).kind, decision.kind)

    def test_native_wrong_types_subclasses_and_metaclasses_do_not_execute_hooks(self):
        manifest = _manifest()
        guard = _guard(manifest)
        load = _load(manifest)
        evidence = _old_evidence(manifest, guard)
        calls = []

        class TypeTrap(type):
            def __eq__(self, other):
                calls.append("type.eq")
                raise AssertionError("native guard metaclass equality must not run")

            def __hash__(self):
                calls.append("type.hash")
                raise AssertionError("native guard metaclass hash must not run")

        class ObjectTrap(metaclass=TypeTrap):
            def __str__(self):
                calls.append("object.str")
                raise AssertionError("native guard conversion must not run")

        class GuardSubclass(CurrentGuardScope):
            pass

        inherited = GuardSubclass(guard.guard_id, guard.service_instance, guard.runtime, guard.birth)
        bomb = ObjectTrap()
        operations = (lambda: evaluate_guard(bomb, guard, evidence), lambda: GuardDecision(load, bomb, evidence),
                      lambda: evaluate_guard(load, guard, bomb), lambda: evaluate_guard(load, inherited, evidence),
                      lambda: OwnerObservation(bomb, guard, manifest.known_owners[0], "EXITED"),
                      lambda: NameObservation(manifest.ref, guard, bomb, "ABSENT"),
                      lambda: GuardEvidence(owners=(bomb,)), lambda: RuntimeResetStatement(manifest.ref, guard, bomb, "a" * 64))
        for index, operation in enumerate(operations):
            with self.subTest(index=index), self.assertRaises(GuardInputError):
                operation()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

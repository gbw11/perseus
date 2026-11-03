"""Independent attached v2 oracle validates bytes from the production C adapter."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "platform/guests/zephyr/telemetry"
CORE = BASE.parent / "diagnostics"


class TelemetryModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("telemetry_v2_oracle", BASE / "contract/tools/validate_contract.py")
        cls.oracle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.oracle)

    def test_attached_golden(self):
        result = subprocess.run([shutil.which("python3"), "-B", str(BASE / "contract/tools/validate_contract.py"), "--self-test"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_bytes(self):
        self.compile_and_validate(False)

    def test_sanitized_production_bytes(self):
        self.compile_and_validate(True)

    def compile_and_validate(self, sanitized):
        with tempfile.TemporaryDirectory(prefix="xenova-telemetry-") as directory:
            binary = Path(directory) / "producer"
            command = [shutil.which("cc"), "-std=c11", "-Wall", "-Wextra", "-Werror", "-g",
                       "-I" + str(BASE / "include"), "-I" + str(CORE / "include"),
                       str(CORE / "src/core.c"), str(CORE / "src/resources_core.c"),
                       str(BASE / "src/clock.c"), str(BASE / "src/writer.c"), str(BASE / "src/model.c"),
                       str(BASE / "tests/emit.c"), "-o", str(binary)]
            if sanitized:
                command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout.decode() + result.stderr.decode())
            self.assertIn(b"PASS production model", result.stderr)
            received = []
            v1 = []
            for line in result.stdout.splitlines():
                row = json.loads(line, object_pairs_hook=self.oracle.reject_duplicate_keys)
                message = row["message"]
                raw = line.split(b'"message":', 1)[1][:-1]
                if message['type'] == 'agent.metrics':
                    self.assertEqual(row['topic'], f"xenova/v1/vms/{message['vm_id']}/metrics")
                    self.assertEqual((row['qos'], row['retain']), (0, False))
                    self.assertLessEqual(len(raw), 1024)
                    v1.append(message)
                    continue
                self.oracle.validate(message, row["topic"], row["qos"], row["retain"], len(raw))
                received.append(message)
            self.assertEqual({m["type"] for m in received}, {"telemetry." + s for s in self.oracle.STREAMS})
            samples = [s for m in received if m["type"] == "telemetry.metrics" for s in m["payload"].get("samples", [])]
            self.assertTrue(any(s["name"] == "loop.last_jitter_us" and s["value"] == "-100" for s in samples))
            self.assertTrue(any(s["name"] == "memory.kernel_heap.used_bytes" and s["value"] == "9007199254741003" for s in samples))
            def values_for(family):
                return [{s['name']: s for s in m['payload']['samples']} for m in received
                        if m['type'] == 'telemetry.metrics' and m['payload']['family'] == family]

            memory = values_for('memory')
            accounted = 'memory.kernel_heap.accounted_bytes'
            self.assertEqual([(m[accounted]['status'], m[accounted]['value']) for m in memory],
                             [('ok', '9007199254743003'), ('collection_failed', None), ('unsupported', None)])
            network = values_for('network')
            counter, tx, rate = 'net_rx_bytes_total', 'net_tx_bytes_total', 'rx_bytes_per_s'
            self.assertTrue(all(counter not in sample and tx not in sample for sample in network))
            self.assertEqual(network[0][rate]['value'], '12345')
            self.assertEqual(network[0][rate]['labels'], {'interface': 'eth0', 'counter_epoch': '7'})
            self.assertEqual(network[1][rate]['value'], '0')
            self.assertEqual(network[2][rate]['status'], 'no_sample')
            self.assertIsNone(network[2][rate]['value'])
            self.assertEqual(network[3][rate]['status'], 'counter_discontinuity')
            self.assertIsNone(network[3][rate]['value'])
            for failed in network[4:]:
                self.assertEqual(failed[rate]['status'], 'collection_failed')
                self.assertIsNone(failed[rate]['value'])
            self.assertEqual([m['seq'] for m in v1], [1, 2, 3, 4, 5])
            self.assertEqual({m['schema_version'] for m in v1}, {'1.0'})
            self.assertEqual(len({uuid.UUID(m['message_id']) for m in v1}), len(v1))
            self.assertIsNone(v1[0]['sent_at'])
            self.assertEqual(v1[1]['sent_at'], '2026-01-01T00:00:00.000Z')
            self.assertEqual(v1[0]['payload']['observed_uptime_ms'], '50000')
            for m in v1:
                self.assertNotIn('processes', m['payload'])
                self.assertEqual({v['name'] for v in m['payload']['values']}, {counter, tx})
            totals = [m['payload']['values'] for m in v1]
            self.assertEqual([v['value'] for v in totals[0]], ['9007199254741111', '18446744073709551615'])
            self.assertEqual(totals[0][0]['labels'], {'iface': 'eth0', 'counter_epoch': '7'})
            self.assertEqual(totals[2][0]['value'], '0')
            self.assertEqual(totals[2][0]['labels']['counter_epoch'], '8')
            for values, reason in zip(totals[3:], ['stale_sample', 'read_failed'], strict=True):
                self.assertTrue(all(v['value'] is None and v['unavailable_reason'] == reason for v in values))
            self.assertTrue(any(s["status"] == "collection_failed" for s in samples))
            self.assertTrue(any(s["status"] == "not_registered" for s in samples))
            # Negative mutations of actual producer output exercise the closed
            # 2.1 contract independently of the C encoder's implementation.
            from copy import deepcopy
            message = next(m for m in received if m['type'] == 'telemetry.metrics'
                           and m['payload']['family'] == 'network')
            topic = f"xenova/v2/vms/{message['vm_id']}/telemetry/metrics"
            for label in ('0', 'not-an-epoch'):
                changed = deepcopy(message)
                next(s for s in changed['payload']['samples'] if s['name'] == rate)['labels']['counter_epoch'] = label
                with self.assertRaises(self.oracle.ContractError):
                    self.oracle.validate(changed, topic, 0, False)
            cpu=next(m for m in received if m['type']=='telemetry.metrics'
                     and m['payload']['family']=='cpu' and m['payload']['samples'][0]['status']=='ok')
            for invalid in ('25.25',-0.001,100.001,float('nan'),float('inf')):
                changed=deepcopy(cpu);changed['payload']['samples'][0]['value']=invalid
                with self.assertRaises(self.oracle.ContractError):
                    self.oracle.validate(changed,f"xenova/v2/vms/{changed['vm_id']}/telemetry/metrics",0,False)
            changed=deepcopy(cpu);changed['payload']['window']['part_count']+=1
            with self.assertRaises(self.oracle.ContractError):
                self.oracle.validate(changed,f"xenova/v2/vms/{changed['vm_id']}/telemetry/metrics",0,False)
            changed=deepcopy(cpu);changed['payload']['unknown_field']=1
            with self.assertRaises(self.oracle.ContractError):
                self.oracle.validate(changed,f"xenova/v2/vms/{changed['vm_id']}/telemetry/metrics",0,False)
            pages=[m for m in received if m['type']=='telemetry.metrics' and m['payload'].get('family')=='cpu'
                   and m['payload']['window']['rows_returned']==32 and m['payload']['samples'][0]['status']=='ok']
            self.assertEqual(len(pages),8)
            self.assertEqual(len({m['payload']['collection']['sample_id'] for m in pages}),1)
            self.assertEqual({m['payload']['window']['threads_status'] for m in pages},{'truncated'})
            self.assertEqual({m['payload']['window']['rows_seen'] for m in pages},{73})
            consumer=self.oracle.ReferenceConsumer();consumer.bind(cpu['vm_id'],cpu['boot_id'],cpu['publisher_instance_id'])
            for page in pages[:7]:self.assertEqual(consumer.ingest(page,1000),'CURRENT_OBSERVATION')
            # A valid page is not proof of full thread coverage. Missing last
            # page remains missing; repeated source windows cannot renew leases.
            group=next(iter(consumer.cpu_parts.values()));self.assertEqual(len(group),7)
            self.assertNotEqual(len(group),pages[0]['payload']['window']['part_count'])
            self.assertEqual(consumer.freshness(pages[0],7001),'STALE')
            self.assertEqual(consumer.ingest(pages[7],7002),'CURRENT_OBSERVATION');self.assertEqual(len(group),8)
            changed=deepcopy(pages[7]);changed['seq']=str(int(changed['seq'])+100)
            changed['message_id']=changed['message_id'].rsplit('/',1)[0]+'/'+changed['seq']
            changed['payload']['window']['start_uptime_ms']='49001'
            self.assertEqual(consumer.ingest(changed,7003),'CPU_SAMPLE_CONFLICT')
            if os.environ.get("XENOVA_TEST_LOG_DIR"):
                out = Path(os.environ["XENOVA_TEST_LOG_DIR"])
                out.mkdir(parents=True, exist_ok=True)
                (out / ("telemetry-model-sanitized.jsonl" if sanitized else "telemetry-model.jsonl")).write_bytes(result.stdout)


class TelemetryBootstrapTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("telemetry_bootstrap", ROOT / "platform/scripts/provision-xenova-telemetry.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix="xenova-telemetry-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.context = {"vm_id": str(uuid.uuid4()), "host_boot_id": str(uuid.uuid4()),
                        "execution_id": str(uuid.uuid4()), "domid": 42, "xen_unique_id": "18446744073709551615"}
        self.credential = {"vm_id": self.context["vm_id"], "broker_ipv4": "192.0.2.1", "port": 18883,
                           "username": "telemetry-" + self.context["vm_id"], "password": "synthetic-unit-test-password",
                           "trusted_internal_plaintext": True}
        self.source = {"inode": 123, "mtime_ns": 456, "bytes": 100}
        self.calls = []
        class Store:
            def apply(inner, *args):
                self.calls.append(args)
        self.store = Store()

    def apply(self, path, previous=None, observe=None, source=None):
        return self.module.provision(self.context, self.credential, source or self.source,
                                     self.directory / path, self.directory / previous if previous else None,
                                     store=self.store, observe=observe or (lambda: dict(self.context)))

    def test_execution_context_retries_only_unaccepted_busy_reads(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        busy = {"ok": False, "operation_id": None, "error": {
            "code": "OPERATION_IN_PROGRESS", "retryable": True, "details": {}}}
        view = {"guest_kind": "zephyr", "shutdown_requested": False,
                "domid": self.context["domid"], "execution_context": {**self.context, "phase": "observed"}}
        manager = Mock()
        manager.call.side_effect = [busy, {"ok": True, "data": {"vm": view}}]
        identity = {"domid": self.context["domid"], "xen_unique_id": self.context["xen_unique_id"]}
        with patch.dict("sys.modules", {"xenova_management": SimpleNamespace(Manager=lambda: manager)}), \
                patch.object(self.module.Path, "read_text", return_value=self.context["host_boot_id"]), \
                patch.object(self.module.subprocess, "check_output", return_value=json.dumps(identity)), \
                patch.object(self.module.time, "sleep") as wait:
            self.assertEqual(self.module.current(self.context["vm_id"]), self.context)
        self.assertEqual(manager.call.call_count, 2)
        wait.assert_called_once_with(0.2)

    def test_execution_context_busy_retry_is_bounded(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        manager = Mock()
        manager.call.return_value = {"ok": False, "operation_id": None, "error": {
            "code": "OPERATION_IN_PROGRESS", "retryable": True, "details": {}}}
        with patch.dict("sys.modules", {"xenova_management": SimpleNamespace(Manager=lambda: manager)}), \
                patch.object(self.module.time, "sleep") as wait:
            with self.assertRaises(ValueError):
                self.module.current(self.context["vm_id"])
        self.assertEqual(manager.call.call_count, 151)
        self.assertEqual(wait.call_count, 150)

    def test_execution_context_identity_failure_is_not_retried(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        manager = Mock()
        manager.call.return_value = {"ok": False, "operation_id": None, "error": {
            "code": "TARGET_CHANGED", "retryable": False, "details": {}}}
        with patch.dict("sys.modules", {"xenova_management": SimpleNamespace(Manager=lambda: manager)}), \
                patch.object(self.module.time, "sleep") as wait:
            with self.assertRaises(ValueError):
                self.module.current(self.context["vm_id"])
        manager.call.assert_called_once()
        wait.assert_not_called()

    def test_private_receipt_idempotency_and_no_secret(self):
        first = self.apply("first.json")
        second = self.apply("first.json")
        self.assertEqual(first, second)
        self.assertEqual(self.calls[0][1], self.calls[1][1])
        receipt = (self.directory / "first.json").read_text()
        self.assertNotIn(self.credential["password"], receipt + json.dumps(first))
        self.assertEqual((self.directory / "first.json").stat().st_mode & 0o777, 0o600)

    def test_drive_channel_shares_guest_boot_but_not_publisher_or_receipt(self):
        telemetry = self.apply("telemetry.json")
        self.store.channel = "drive"
        def drive(name, authority="telemetry.json"):
            return self.module.provision(self.context, self.credential, self.source, self.directory / name,
                                         store=self.store, observe=lambda: dict(self.context), channel="drive",
                                         guest_boot_receipt=self.directory / authority)
        first = drive("drive.json")
        self.assertEqual(first["boot_id"], telemetry["boot_id"])
        self.assertNotEqual(first["publisher_instance_id"], telemetry["publisher_instance_id"])
        self.assertNotEqual(first["receipt_id"], telemetry["receipt_id"])
        self.assertEqual(first, drive("drive.json"))
        with self.assertRaises(ValueError):
            drive("invalid.json", "drive.json")
        self.context["execution_id"] = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            drive("stale.json")

    def test_receipts_cannot_cross_channels_or_omit_boot_authority(self):
        self.apply("telemetry.json")
        self.store.channel = "drive"
        for receipt, authority in (("drive.json", None), ("telemetry.json", "telemetry.json")):
            with self.assertRaises(ValueError):
                self.module.provision(self.context, self.credential, self.source, self.directory / receipt,
                                      store=self.store, observe=lambda: self.context, channel="drive",
                                      guest_boot_receipt=self.directory / authority if authority else None)

    def test_publisher_restart_preserves_boot_and_execution(self):
        first = self.apply("first.json")
        second = self.apply("second.json", "first.json")
        self.assertEqual(first["boot_id"], second["boot_id"])
        self.assertEqual(first["context"], second["context"])
        self.assertNotEqual(first["publisher_instance_id"], second["publisher_instance_id"])
        self.assertEqual(self.calls[-1][2]["publisher_id"], first["publisher_instance_id"])

    def test_changed_context_refuses_and_post_write_uncertainty_is_durable(self):
        changed = {**self.context, "xen_unique_id": "1"}
        with self.assertRaises(ValueError):
            self.apply("changed-before.json", observe=lambda: changed)
        self.assertEqual(self.calls, [])
        values = iter([self.context, changed])
        with self.assertRaises(ValueError):
            self.apply("changed-after.json", observe=lambda: next(values))
        self.assertEqual(json.loads((self.directory / "changed-after.json").read_text())["phase"], "uncertain")
        with self.assertRaises(ValueError):
            self.apply("changed-after.json")
        self.assertEqual(len(self.calls), 1)

    def test_new_boot_or_changed_credentials_cannot_reuse_receipt(self):
        self.apply("first.json")
        self.context["host_boot_id"] = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            self.apply("first.json")
        self.assertEqual(len(self.calls), 1)

    def test_store_outcome_uncertainty_prevents_blind_retry(self):
        def uncertain(*args):
            raise OSError("commit response lost")
        self.store.apply = uncertain
        with self.assertRaises(OSError):
            self.apply("uncertain.json")
        self.assertEqual(json.loads((self.directory / "uncertain.json").read_text())["phase"], "uncertain")
        with self.assertRaises(ValueError):
            self.apply("uncertain.json")

    def test_private_credentials_mode_identity_and_plaintext_boundary(self):
        path = self.directory / "credentials.json"
        path.write_text(json.dumps(self.credential))
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.module.credentials(path, self.context["vm_id"])
        path.chmod(0o600)
        self.assertEqual(self.module.credentials(path, self.context["vm_id"])[0], self.credential)
        with self.assertRaises(ValueError):
            self.module.credentials(path, str(uuid.uuid4()))
        path.write_text(json.dumps({**self.credential, "trusted_internal_plaintext": False}))
        with self.assertRaises(ValueError):
            self.module.credentials(path, self.context["vm_id"])

    def test_broker_account_namespace_is_bound_to_selected_channel(self):
        path = self.directory / 'account.json'
        path.write_text(json.dumps(self.credential))
        path.chmod(0o600)
        self.module.credentials(path, self.context['vm_id'], 'telemetry')
        self.module.credentials(path, self.context['vm_id'])
        with self.assertRaises(ValueError):
            self.module.credentials(path, self.context['vm_id'], 'drive')
        path.write_text(json.dumps({**self.credential, 'username': self.context['vm_id']}))
        self.module.credentials(path, self.context['vm_id'], 'drive')
        self.module.credentials(path, self.context['vm_id'])
        with self.assertRaises(ValueError):
            self.module.credentials(path, self.context['vm_id'], 'telemetry')
        self.module.credentials(path, self.context['vm_id'], 'telemetry', for_reconciliation=True)
        path.write_text(json.dumps({**self.credential, 'username': 'telemetry-' + str(uuid.uuid4())}))
        with self.assertRaises(ValueError):
            self.module.credentials(path, self.context['vm_id'], 'telemetry')

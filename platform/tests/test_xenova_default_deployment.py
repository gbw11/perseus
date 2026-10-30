"""새 최초 설치 배치와 템플릿 보관을 작은 자산·Xen 경계 대역으로 검증한다."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import Board, FILES, ROOT, assemble, contract, vm
from test_xenova_lifecycle import Xen, application_bundle, bundle

DRIVE_IDS = {"zephyr-drive-a", "zephyr-drive-b"}


class DefaultDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_defaults_are_debian_interaction_and_two_autostart_starters(self):
        defaults = json.loads((FILES / "defaults.json").read_text())
        self.assertEqual([r["id"] for r in defaults["instances"]], ["debian-dev"])
        self.assertEqual(defaults["instances"][0]["display_name"], "Debian Interaction")
        firmware = defaults["firmware_instances"]
        self.assertEqual({r["id"] for r in firmware}, DRIVE_IDS)
        self.assertEqual(len({r["template"] for r in firmware}), 1)
        self.assertTrue(all(r["autostart"] and r["board_shutdown_policy"] == "graceful" for r in firmware))
        self.assertTrue(defaults["instances"][0]["autostart"])
        self.assertEqual(defaults["instances"][0]["ipv4"], "192.168.77.20")

    def test_factory_versions_match_selected_catalog_and_editable_recipes_do_not_regress(self):
        selected = json.loads((ROOT / "platform/guests/default-templates.lock.json").read_text())["templates"]
        defaults = json.loads((FILES / "defaults.json").read_text())
        by_id = {e["template_id"]: e for e in selected}
        for record in defaults["instances"] + defaults["firmware_instances"]:
            self.assertEqual(record["template_version"], by_id[record["template_id"]]["version"])
        for entry in selected:
            if entry["guest_kind"] == "linux":
                recipe = ROOT / "platform/guests/linux" / entry["id"] / "recipe.json"
                # recipe는 다음 게시본의 입력이다. 편집만으로 기존 선택 lock/영수증을 바꾸지 않는다.
                current = json.loads(recipe.read_text())["version"]
                version = lambda v: tuple(int(n) for n in v.split("-")[0].split("+")[0].split("."))
                self.assertGreaterEqual(version(current), version(entry["version"]))
                self.assertRegex(entry["manifest_sha256"], r"^[0-9a-f]{64}$")

    def test_capacity_separates_retained_ubuntu_from_default_disks(self):
        templates = [{"logical_bytes": 8 * contract.GIB, "allocated_bytes": 1024, "boot_bytes": 4096}] * 2
        result = contract.capacity(4 * contract.GIB, [templates[1]], retained_templates=templates,
                                   retained_firmware_bytes=3 * contract.MIB)
        self.assertEqual(result["vm_max_bytes"], 8 * contract.GIB)
        self.assertEqual(result["retained_template_root_bytes"], 16 * contract.GIB)
        self.assertEqual(result["boot_catalog_bytes"], 3 * 4096)
        self.assertEqual(result["retained_firmware_bytes"], 3 * contract.MIB)

    def current_board(self, *, network=False):
        board = Board(self.base / "board")
        board.grow_mount()
        factory = board.factory()
        factory["instances"] = [s for s in factory["instances"] if s["record"]["id"] == "debian-dev"]
        shutil.rmtree(board.data / "seeds/ubuntu-dev")
        record = factory["instances"][0]["record"]
        record["display_name"] = "Debian Interaction"
        (board.data / "seeds/debian-dev/instance.json").write_text(json.dumps(record))
        if network:
            from test_xenova_network_zephyr import network_bundle
            source = network_bundle(bundle(self.base / "diagnostic"), self.base / "starter")
        else:
            source = application_bundle(bundle(self.base / "diagnostic"), self.base / "starter")
        manifest = source / "firmware.json"
        meta = json.loads(manifest.read_text())
        lock = json.loads((ROOT / ("platform/guests/zephyr/network-starter.lock.json" if network else "platform/guests/zephyr/starter.lock.json")).read_text())
        meta.update(id=lock["id"], kind="zephyr-xen-firmware", version=lock["version"], template_version="1.1.0" if network else "1.0.1")
        meta["source"].update({k: lock[k] for k in ("app", "app_files", "app_source_sha256", "zephyr_commit", "sdk_version")})
        meta["source"]["source_revision"] = "sha256:" + lock["app_source_sha256"]
        manifest.chmod(0o644)
        manifest.write_text(json.dumps(meta))
        manifest.chmod(0o444)
        entry = assemble.rtos.info(source, assemble.sha(manifest))
        factory["rtos_templates"] = assemble.copy_rtos_templates(board.data, [entry])
        factory["firmware_instances"] = assemble.copy_rtos_seeds(board.data, [entry])
        factory["features"] = contract.FEATURES
        (board.data / ".xenova-install.json").write_text(json.dumps(factory))
        return board, factory

    def test_first_registration_rerun_preserves_three_identities_and_independent_firmware(self):
        board, factory = self.current_board()
        board.p.register()
        instances = vm.load_records(board.data)
        self.assertEqual(set(instances), DRIVE_IDS | {"debian-dev"})
        self.assertEqual(len({r["uuid"] for r in instances.values()}), 3)
        first = {key: r["uuid"] for key, r in instances.items()}
        a, b = [board.data / "instances" / key / "firmware" / instances[key]["active_firmware"]["revision"] / "zephyr.bin"
                for key in sorted(DRIVE_IDS)]
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertNotEqual(a.stat().st_ino, b.stat().st_ino)
        board.p.register()
        self.assertEqual({k: r["uuid"] for k, r in vm.load_records(board.data).items()}, first)
        self.assertFalse((board.data / "instances/ubuntu-dev").exists())
        self.assertTrue((board.data / "templates/ubuntu-24.04-20260926/root.raw").is_file())
        self.assertEqual(len(factory["firmware_instances"]), 2)

    def test_autostart_starts_both_zephyr_domains_with_separate_configs(self):
        board, _ = self.current_board()
        board.p.register()
        records = vm.load_records(board.data)
        xen = Xen(board.data, records)
        runtime = board.root / "run/xenova"
        platform = vm.Platform(board.data, runtime, xen, defaults_path=FILES / "defaults.json")
        with patch.object(platform, "check_xen"), patch.object(platform, "check_network"), patch.object(vm, "storage_check"):
            platform.autostart(records, sorted(records))
        self.assertEqual(len(xen.rows), 3)
        for key in DRIVE_IDS:
            cfg = (runtime / (key + ".cfg")).read_text()
            self.assertIn(records[key]["uuid"], cfg)
            self.assertIn(str(board.data / "instances" / key), cfg)
            self.assertNotIn("disk =", cfg)
            self.assertNotIn("vif =", cfg)
        result = json.loads((runtime / "autostart-results.json").read_text())
        self.assertTrue(all(result[key]["result"] == "running" for key in DRIVE_IDS))

    def test_identity_planning_interruption_reuses_all_three_uuids(self):
        board, _ = self.current_board()
        save = board.p.save
        state_path = board.data / ".first-install-state.json"
        def interrupted(path, value):
            save(path, value)
            if path == state_path and value.get("phase") == "identities-planned":
                raise ValueError("interrupted after durable identity plan")
        with patch.object(board.p, "save", side_effect=interrupted):
            with self.assertRaisesRegex(ValueError, "durable identity plan"):
                board.p.register()
        identities = json.loads(state_path.read_text())["identities"]
        board.p.register()
        self.assertEqual({k: r["uuid"] for k, r in vm.load_records(board.data).items()},
                         {k: v["uuid"] for k, v in identities.items()})

    def test_plan_retains_ubuntu_but_only_budgets_one_linux_seed(self):
        from first_install_fixture import starter_bundle
        source = starter_bundle(self.base / "pinned-starter")
        entry = assemble.rtos.info(source, assemble.sha(source / "firmware.json"))
        config = json.loads((ROOT / "platform/first-install/example.config.json").read_text())
        config["rtos_templates"] = [{"template_dir": str(source), "template_manifest_sha256": entry["template_manifest_sha256"]}]
        image = self.base / "platform" / "source.wic"
        info = {"logical_bytes": 8 * contract.GIB, "allocated_bytes": 2048, "boot_bytes": 8192,
                "preparation_scratch_bytes": 0}
        def template_info(directory, source_id, expected):
            return {**info, "source_id": source_id, "template": directory, "template_manifest_sha256": expected}
        with patch.object(assemble, "template_info", side_effect=template_info), \
             patch.object(assemble, "read_layout", return_value=(b"", 4 * contract.GIB, {})):
            result = assemble.plan(config, image, {"build_directory": str(self.base / "build"), "source_commit": "a" * 40},
                                   self.base / "release", self.base / "work", None)
        self.assertEqual(len(result["linux_templates"]), 2)
        self.assertEqual([r["id"] for r in result["guests"]], ["debian-dev"])
        self.assertEqual(result["capacity"]["vm_max_bytes"], 8 * contract.GIB)
        self.assertEqual(result["capacity"]["retained_template_root_bytes"], 16 * contract.GIB)
        self.assertEqual(result["capacity"]["retained_firmware_bytes"], entry["logical_bytes"] * 3 + 2 * 65536)
        self.assertFalse((self.base / "release").exists())

    def test_common_cli_reads_registry_and_requires_all_three_factory_vms(self):
        board, _ = self.current_board()
        board.p.register()
        records = vm.load_records(board.data)
        xen = Xen(board.data, records)
        platform = vm.Platform(board.data, board.root / "run/xenova", xen, defaults_path=FILES / "defaults.json")
        with patch.object(vm, "Platform", return_value=platform), patch.object(vm, "storage_check"), \
             patch.object(platform, "check_xen"), patch.object(platform, "check_network"), \
             patch.object(platform, "autostart", wraps=platform.autostart) as start:
            vm.main(["autostart"])
        self.assertEqual(set(start.call_args.args[0]), DRIVE_IDS | {"debian-dev"})
        self.assertEqual(set(start.call_args.args[1]), DRIVE_IDS | {"debian-dev"})
        self.assertEqual(len(xen.rows), 3)

    def test_catalog_keeps_ubuntu_without_an_ubuntu_seed(self):
        import xenova_catalog as catalog
        board, _ = self.current_board(network=True)
        selected = json.loads((ROOT / "platform/guests/default-templates.lock.json").read_text())["templates"]
        versions = {entry["id"]: entry["version"] for entry in selected}
        for directory in (board.data / "templates").iterdir():
            meta = json.loads((directory / "template.json").read_text())
            boot_path = directory / "boot.json"
            boot = json.loads(boot_path.read_text())
            boot["source"] = meta["source"]
            boot_path.chmod(0o644)
            boot_path.write_text(json.dumps(boot))
            boot_path.chmod(0o444)
            names = ("root.raw", "Image", "initrd", "kernel.config", "boot.json")
            meta.update(version=versions[directory.name], files={n: {"sha256": assemble.sha(directory / n),
                        "bytes": (directory / n).stat().st_size} for n in names})
            path = directory / "template.json"
            path.write_text(json.dumps(meta))
            path.chmod(0o444)
        assemble.copy_management_catalog(board.data)
        rows = catalog.entries(board.data)
        self.assertEqual({e["template_id"] for e in rows},
                         {"ubuntu-24.04-arm64", "debian-13-arm64", "zephyr-xenvm-starter-arm64"})
        self.assertEqual({e["template_id"]: e["template_version"] for e in rows},
                         {e["template_id"]: e["version"] for e in selected})
        self.assertFalse((board.data / "seeds/ubuntu-dev").exists())

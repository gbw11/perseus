"""실제 C pool/engine의 time lease 갱신과 source fence를 구분한다."""
import json
from pathlib import Path
import subprocess

import pytest

from test_world import fixture, parking_world
from world_codec import encode
from test_engine import modern_world


HOME = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def refresh_probe(tmp_path_factory):
    build = tmp_path_factory.mktemp("world-trust-refresh")
    binary = build / "trust-refresh-probe"
    world = HOME / "world"
    planner = HOME / "planner"
    sources = [world / "world.c", world / "generated/world.pb.c",
               *(world / "nanopb" / name for name in ("pb_common.c", "pb_decode.c", "pb_encode.c")),
               planner / "lattice.c", planner / "engine.c", planner / "generated/primitives.c",
               HOME / "tests/trust_refresh_probe.c"]
    command = ["cc", "-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
               "-fsanitize=address,undefined", '-DTEST_PRIMITIVE_SHA="' + fixture.PRIMITIVE + '"',
               *("-I" + str(path) for path in (world, world / "generated", world / "nanopb", planner)),
               *map(str, sources), "-lm", "-o", str(binary)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    return binary, build


def product(probe, case, value):
    binary, build = probe
    path = build / "input.pb"
    path.write_bytes(encode(value))
    result = subprocess.run([str(binary), case, str(path)], check=True, capture_output=True,
                            text=True, timeout=15)
    assert json.loads(result.stdout) == {"case": case, "result": "OK"}


def world(feature):
    if feature == 11:
        return modern_world()
    if feature == 15:
        return parking_world()
    value = fixture.world()
    if feature == 1:
        from xenova_drive_world import mark_road
        from world_codec import schema
        mark_road(value, [schema.Point(x=p.x, y=1.96) for p in value.reference],
                  [schema.Point(x=p.x, y=1.24) for p in value.reference])
    return value


@pytest.mark.parametrize("feature", [0, 1, 11, 15])
def test_same_source_renewal_keeps_held_reader_writer_and_original_deadline(refresh_probe, feature):
    product(refresh_probe, "pool", world(feature))


def test_every_semantic_identity_or_policy_change_fences_but_storage_padding_does_not(refresh_probe):
    product(refresh_probe, "identity", fixture.world())


def test_invalid_time_interval_expiry_or_native_identity_never_mutates_trust(refresh_probe):
    product(refresh_probe, "invalid", fixture.world())


@pytest.mark.parametrize("feature", [0, 1])
def test_clock_renewal_during_actual_active_plan_keeps_result_and_original_expiry(refresh_probe, feature):
    product(refresh_probe, "engine-renew", world(feature))


def test_real_clock_epoch_change_during_actual_active_plan_fences_result(refresh_probe):
    product(refresh_probe, "engine-change", fixture.world())

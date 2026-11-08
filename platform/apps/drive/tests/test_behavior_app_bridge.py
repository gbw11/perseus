"""Persistent actual app/input C bridge for Nano actor integration.

This module supplies a build fixture, not synthetic READY or accepted clock.
Clock/P4 bytes enter actual input callbacks; main.c and this bridge share the
actual lifecycle classifier. RTOS scheduling/transport/bootstrap/plant remain
declared leaves. No local PC execution is part of static preparation.
"""
from pathlib import Path
import subprocess
import pytest
from test_input_clock import HOME, RUNTIME


@pytest.fixture(scope='module')
def app_bridge_binary(tmp_path_factory):
    build=tmp_path_factory.mktemp('behavior-app-bridge'); include=build/'include'
    for directory in ('xenova','zephyr/sys','zephyr/random','zephyr/logging'): (include/directory).mkdir(parents=True,exist_ok=True)
    runtime=RUNTIME.replace('struct xd_inputs {bool connected;',
        'struct xd_inputs {char app_instances[2][37],vm_id[37];bool connected;')
    for target,text in {
        'xenova/drive_runtime.h':runtime,
        'xenova/drive_context.h':'#include <xenova/drive_runtime.h>\n',
        'zephyr/sys/atomic.h':'#include <xenova/drive_runtime.h>\n',
        'zephyr/random/random.h':'#include <stddef.h>\nvoid sys_rand_get(void *,size_t);\n',
        'zephyr/logging/log.h':'#define LOG_LEVEL_INF 3\n#define LOG_MODULE_REGISTER(...)\n'
            'void b08_diagnostic_log(const char *,...);\n#define LOG_INF(...) b08_diagnostic_log(__VA_ARGS__)\n',
        'shim.h':'#define XENOVA_DRIVE_IO_H\n#include <xenova/drive_runtime.h>\nvoid k_yield(void);\n',
    }.items(): (include/target).write_text(text)
    platform=HOME.parents[1]; world=HOME/'world'; planner=HOME/'planner'; mqtt=platform/'apps/common/xenova-mqtt-c'
    includes=(include,HOME/'zephyr/src',planner,world,world/'generated',world/'nanopb',world/'tests',
        mqtt/'include',mqtt/'third_party/jsmn',platform/'apps/common/xenova-drive-runtime/include',
        platform/'apps/autonomous/zephyr/src',platform/'apps/manual/zephyr/src',platform/'guests/zephyr/diagnostics/include')
    sources=[HOME/'tests/behavior_app_bridge.c',world/'tests/input_bridge.c',
        *[planner/name for name in ('task_reference.c','behavior.c','parking.c','lattice.c','engine.c','generated/primitives.c')],
        HOME/'zephyr/src/adapter.c',HOME/'zephyr/src/controller.c',platform/'apps/autonomous/zephyr/src/autonomous.c',
        platform/'apps/manual/zephyr/src/manual.c',world/'world.c',world/'generated/world.pb.c',
        *[world/'nanopb'/name for name in ('pb_common.c','pb_decode.c','pb_encode.c')],
        *sorted((mqtt/'src').glob('*.c')),platform/'guests/zephyr/diagnostics/src/health.c']
    binary=build/'behavior-app'
    result=subprocess.run(['cc','-std=c11','-D_POSIX_C_SOURCE=200809L','-DCONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS=1',
        '-O1','-g','-Wall','-Wextra','-Werror',
        '-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-fsanitize=address,undefined',
        '-include',str(include/'shim.h'),*['-I'+str(p) for p in includes],*map(str,sources),'-lm','-o',str(binary)],
        capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stderr
    return binary

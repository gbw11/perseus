# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
set(DRIVE ${CMAKE_CURRENT_SOURCE_DIR}/../..)
set(GUEST ${DRIVE}/../../guests/zephyr)
list(APPEND ZEPHYR_EXTRA_MODULES ${GUEST}/diagnostics)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_app_memory_acceptance)
target_include_directories(app PRIVATE ${DRIVE}/world ${DRIVE}/world/generated
 ${DRIVE}/world/nanopb ${DRIVE}/planner ${DRIVE}/zephyr/src ${GUEST}/telemetry/include)
target_sources(app PRIVATE src/main.c ${DRIVE}/zephyr/src/lattice_memory.c
 ${DRIVE}/world/world.c ${DRIVE}/world/generated/world.pb.c
 ${DRIVE}/world/nanopb/pb_common.c ${DRIVE}/world/nanopb/pb_decode.c ${DRIVE}/world/nanopb/pb_encode.c
 ${DRIVE}/planner/task_reference.c ${DRIVE}/planner/behavior.c ${DRIVE}/planner/parking.c
 ${DRIVE}/planner/lattice.c ${DRIVE}/planner/engine.c ${DRIVE}/planner/generated/primitives.c
 ${GUEST}/telemetry/src/clock.c ${GUEST}/telemetry/src/writer.c
 ${GUEST}/telemetry/src/model.c ${GUEST}/telemetry/src/adapter.c)

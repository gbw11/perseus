# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
list(APPEND ZEPHYR_EXTRA_MODULES ${CMAKE_CURRENT_SOURCE_DIR}/../.. ${CMAKE_CURRENT_SOURCE_DIR}/../../../diagnostics)
list(APPEND ZEPHYR_EXTRA_MODULES ${CMAKE_CURRENT_SOURCE_DIR}/../../../../../apps/common/xenova-mqtt-c
 ${CMAKE_CURRENT_SOURCE_DIR}/../../../../../apps/common/xenova-drive-runtime)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_telemetry_native_acceptance)
target_sources(app PRIVATE src/main.c)

# Actual app resource owner and production Diagnostics bridge, real sockets.
set(DRIVE ${CMAKE_CURRENT_SOURCE_DIR}/../../../../../apps/drive)
target_include_directories(app PRIVATE ${DRIVE}/world ${DRIVE}/world/generated
 ${DRIVE}/world/nanopb ${DRIVE}/planner ${DRIVE}/zephyr/src)
target_sources(app PRIVATE ${DRIVE}/zephyr/src/lattice_memory.c
 ${DRIVE}/world/world.c ${DRIVE}/world/generated/world.pb.c
 ${DRIVE}/world/nanopb/pb_common.c ${DRIVE}/world/nanopb/pb_decode.c ${DRIVE}/world/nanopb/pb_encode.c
 ${DRIVE}/planner/lattice.c ${DRIVE}/planner/engine.c ${DRIVE}/planner/generated/primitives.c)

if(CONFIG_XENOVA_DRIVE_WORKERS)
 target_include_directories(app PRIVATE ${DRIVE}/../autonomous/zephyr/src ${DRIVE}/../manual/zephyr/src)
 target_sources(app PRIVATE ${DRIVE}/zephyr/src/workers.c ${DRIVE}/zephyr/src/adapter.c
  ${DRIVE}/../autonomous/zephyr/src/autonomous.c ${DRIVE}/../manual/zephyr/src/manual.c)
endif()

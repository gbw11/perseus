# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
list(APPEND ZEPHYR_EXTRA_MODULES
  ${CMAKE_CURRENT_SOURCE_DIR}/../../common/xenova-mqtt-c
  ${CMAKE_CURRENT_SOURCE_DIR}/../../common/xenova-drive-runtime
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/xen-store
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/xen-runtime
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/diagnostics
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/telemetry)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_drive)
target_include_directories(app PRIVATE ../../autonomous/zephyr/src ../../manual/zephyr/src)
target_sources(app PRIVATE src/main.c src/controller.c src/adapter.c
  ../../autonomous/zephyr/src/autonomous.c ../../manual/zephyr/src/manual.c)
target_sources_ifdef(CONFIG_XENOVA_DRIVE_WORKERS app PRIVATE src/workers.c)
target_sources_ifdef(CONFIG_XENOVA_AUTO_DELAY_INJECTION app PRIVATE src/auto_delay.c)

if(CONFIG_XENOVA_DRIVE_LATTICE)
  set(DRIVE_WORLD ${CMAKE_CURRENT_SOURCE_DIR}/../world)
  set(DRIVE_PLANNER ${CMAKE_CURRENT_SOURCE_DIR}/../planner)
  target_include_directories(app PRIVATE ${DRIVE_WORLD} ${DRIVE_WORLD}/generated
    ${DRIVE_WORLD}/nanopb ${DRIVE_PLANNER})
  target_sources(app PRIVATE src/lattice_app.c src/lattice_input.c src/lattice_memory.c
    ${DRIVE_WORLD}/world.c ${DRIVE_WORLD}/generated/world.pb.c
    ${DRIVE_WORLD}/nanopb/pb_common.c ${DRIVE_WORLD}/nanopb/pb_decode.c ${DRIVE_WORLD}/nanopb/pb_encode.c
    ${DRIVE_PLANNER}/lattice.c ${DRIVE_PLANNER}/engine.c ${DRIVE_PLANNER}/generated/primitives.c)
endif()

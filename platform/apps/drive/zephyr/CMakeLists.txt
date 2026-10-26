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
target_sources(app PRIVATE src/main.c src/controller.c
  ../../autonomous/zephyr/src/autonomous.c ../../manual/zephyr/src/manual.c)

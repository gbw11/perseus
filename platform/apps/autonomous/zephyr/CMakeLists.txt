# SPDX-License-Identifier: Apache-2.0
# Zephyr 자율주행 앱. 보드: native_sim (PC 시험, 호스트 소켓으로 브로커 접속) / xenvm (Xen 게스트 펌웨어)
cmake_minimum_required(VERSION 3.20.5)
list(APPEND ZEPHYR_EXTRA_MODULES
  ${CMAKE_CURRENT_SOURCE_DIR}/../../common/xenova-mqtt-c
  ${CMAKE_CURRENT_SOURCE_DIR}/../../common/xenova-drive-runtime
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/xen-store
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/xen-runtime
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/diagnostics
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr/telemetry)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_autonomous)
target_sources(app PRIVATE src/main.c src/autonomous.c)

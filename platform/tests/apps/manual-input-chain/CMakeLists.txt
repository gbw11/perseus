# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
set(PLATFORM ${CMAKE_CURRENT_SOURCE_DIR}/../../..)
set(DRIVE ${PLATFORM}/apps/drive)
set(RUNTIME ${PLATFORM}/apps/common/xenova-drive-runtime)
set(MQTT ${PLATFORM}/apps/common/xenova-mqtt-c)
list(APPEND ZEPHYR_EXTRA_MODULES ${PLATFORM}/guests/zephyr/diagnostics)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_manual_input_to_result_acceptance)
target_include_directories(app PRIVATE ${RUNTIME}/include ${RUNTIME}/src ${MQTT}/include
 ${MQTT}/ports/zephyr ${MQTT}/src ${MQTT}/third_party/jsmn ${DRIVE}/zephyr/src
 ${PLATFORM}/apps/manual/zephyr/src ${PLATFORM}/apps/autonomous/zephyr/src)
target_compile_definitions(app PRIVATE CONFIG_XENOVA_DRIVE_IO_PRIORITY=8
 CONFIG_XENOVA_DRIVE_WORKER_STACK_SIZE=8192 CONFIG_XENOVA_DRIVE_AUTO_PRIORITY=10
 CONFIG_XENOVA_DRIVE_MANUAL_PRIORITY=6 CONFIG_XENOVA_MQTT_RX_BUF=1280
 CONFIG_XENOVA_MQTT_TX_BUF=1280 CONFIG_XENOVA_MQTT_MAX_SUBS=4
 CONFIG_XENOVA_MQTT_LATEST_SLOTS=4 CONFIG_XENOVA_MQTT_PUB_TOPICS=4)
target_sources(app PRIVATE src/main.c ${DRIVE}/zephyr/src/workers.c ${DRIVE}/zephyr/src/adapter.c
 ${PLATFORM}/apps/manual/zephyr/src/manual.c ${PLATFORM}/apps/autonomous/zephyr/src/autonomous.c
 ${MQTT}/src/xm_topic.c ${MQTT}/src/xm_policy.c ${MQTT}/src/xm_json.c
 ${MQTT}/src/xm_envelope.c ${MQTT}/src/xm_seq.c ${MQTT}/src/xm_payload.c)
target_link_libraries(app PRIVATE m)

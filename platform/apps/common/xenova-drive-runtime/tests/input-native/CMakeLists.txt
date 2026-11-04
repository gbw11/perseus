# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_runtime_input_memory_acceptance)
set(RUNTIME ${CMAKE_CURRENT_SOURCE_DIR}/../..)
set(MQTT ${RUNTIME}/../xenova-mqtt-c)
set(DIAGNOSTICS ${RUNTIME}/../../../guests/zephyr/diagnostics)
target_include_directories(app PRIVATE ${RUNTIME}/include ${MQTT}/include
 ${MQTT}/ports/zephyr ${MQTT}/src ${MQTT}/third_party/jsmn ${DIAGNOSTICS}/include)
target_compile_definitions(app PRIVATE CONFIG_XENOVA_DRIVE_WARM=1
 CONFIG_XENOVA_DRIVE_IO_PRIORITY=8 CONFIG_XENOVA_DIAGNOSTICS_MAX_THREADS=32
 CONFIG_XENOVA_MQTT_RX_BUF=1280
 CONFIG_XENOVA_MQTT_TX_BUF=1280 CONFIG_XENOVA_MQTT_MAX_SUBS=4
 CONFIG_XENOVA_MQTT_LATEST_SLOTS=4 CONFIG_XENOVA_MQTT_PUB_TOPICS=4)
target_sources(app PRIVATE src/main.c ${MQTT}/src/xm_topic.c ${MQTT}/src/xm_policy.c
 ${MQTT}/src/xm_json.c ${MQTT}/src/xm_envelope.c ${MQTT}/src/xm_seq.c ${MQTT}/src/xm_payload.c)

# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
list(APPEND ZEPHYR_EXTRA_MODULES ${CMAKE_CURRENT_SOURCE_DIR}/../xen-store ${CMAKE_CURRENT_SOURCE_DIR}/../xen-runtime ${CMAKE_CURRENT_SOURCE_DIR}/../diagnostics ${CMAKE_CURRENT_SOURCE_DIR}/../telemetry)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_network_starter)
target_sources(app PRIVATE src/main.c)
target_sources_ifdef(CONFIG_XENOVA_DIAGNOSTICS app PRIVATE src/diagnostics.c)
target_sources_ifdef(CONFIG_XENOVA_DIAGNOSTICS_EXERCISE app PRIVATE src/exercise.c)
target_sources_ifdef(CONFIG_XENOVA_TELEMETRY app PRIVATE src/telemetry.c)

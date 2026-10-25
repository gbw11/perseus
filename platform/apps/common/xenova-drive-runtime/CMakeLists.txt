# SPDX-License-Identifier: Apache-2.0
if(CONFIG_XENOVA_DRIVE_RUNTIME)
  zephyr_library_named(xenova_drive_runtime)
  zephyr_include_directories(include ../xenova-mqtt-c/ports/zephyr)
  zephyr_library_sources(src/runtime.c)
endif()

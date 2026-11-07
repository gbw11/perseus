# SPDX-License-Identifier: Apache-2.0
if(CONFIG_XENOVA_DRIVE_RUNTIME)
  zephyr_library_named(xenova_drive_runtime)
  zephyr_include_directories(include ../xenova-mqtt-c/ports/zephyr)
  zephyr_library_sources(src/runtime.c src/io.c)
  zephyr_library_sources_ifdef(CONFIG_XENOVA_AUTO_DELAY_INJECTION src/auto_delay.c)
  zephyr_library_sources_ifdef(CONFIG_XENOVA_RESOURCE_STRESS_INJECTION src/cpu_stress.c src/memory_stress.c)
  zephyr_library_sources_ifdef(CONFIG_XENOVA_DRIVE_WARM src/context.c)
endif()

# SPDX-License-Identifier: Apache-2.0
if(CONFIG_XENOVA_DIAGNOSTICS)
  zephyr_library_named(xenova_diagnostics)
  zephyr_include_directories(include)
  zephyr_library_sources(src/core.c src/diagnostics.c src/resources_core.c src/resources.c src/health.c)
  zephyr_library_sources_ifdef(CONFIG_SHELL src/shell.c)
endif()

# SPDX-License-Identifier: Apache-2.0
if(CONFIG_XENOVA_TELEMETRY)
  zephyr_library_named(xenova_telemetry)
  zephyr_include_directories(include)
  zephyr_library_sources(src/writer.c src/model.c src/adapter.c src/bootstrap.c src/socket_transport.c src/worker.c)
  zephyr_library_sources_ifdef(CONFIG_SHELL src/shell.c)
endif()

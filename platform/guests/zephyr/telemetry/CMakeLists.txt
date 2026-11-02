# SPDX-License-Identifier: Apache-2.0
if(CONFIG_XENOVA_TELEMETRY)
  zephyr_library_named(xenova_telemetry)
  get_filename_component(XENOVA_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../.." ABSOLUTE)
  set(XENOVA_ID_HEADER "${PROJECT_BINARY_DIR}/include/generated/xenova_build_identity.h")
  execute_process(COMMAND ${PYTHON_EXECUTABLE} "${CMAKE_CURRENT_LIST_DIR}/tools/build_identity.py"
    --root "${XENOVA_ROOT}" --zephyr "${ZEPHYR_BASE}" --compiler "${CMAKE_C_COMPILER}"
    --config "${PROJECT_BINARY_DIR}/.config" --output "${XENOVA_ID_HEADER}"
    "--flags=${CMAKE_C_FLAGS}|${CMAKE_EXE_LINKER_FLAGS}"
    COMMAND_ERROR_IS_FATAL ANY)
  # A dirty source change must update the ID even in an incremental Ninja build.
  add_custom_target(xenova_build_identity ALL
    COMMAND ${PYTHON_EXECUTABLE} "${CMAKE_CURRENT_LIST_DIR}/tools/build_identity.py"
      --root "${XENOVA_ROOT}" --zephyr "${ZEPHYR_BASE}" --compiler "${CMAKE_C_COMPILER}"
      --config "${PROJECT_BINARY_DIR}/.config" --output "${XENOVA_ID_HEADER}"
      "--flags=${CMAKE_C_FLAGS}|${CMAKE_EXE_LINKER_FLAGS}"
    BYPRODUCTS "${XENOVA_ID_HEADER}" "${PROJECT_BINARY_DIR}/include/generated/xenova_build_identity.json"
    VERBATIM)
  add_dependencies(xenova_telemetry xenova_build_identity)
  zephyr_include_directories(include)
  zephyr_library_sources(src/writer.c src/model.c src/clock.c src/adapter.c src/bootstrap.c src/socket_transport.c src/worker.c src/build.c)
  zephyr_library_sources_ifdef(CONFIG_SHELL src/shell.c)
  zephyr_library_sources_ifdef(CONFIG_XENOVA_RUNTIME src/xen_bootstrap.c)
endif()

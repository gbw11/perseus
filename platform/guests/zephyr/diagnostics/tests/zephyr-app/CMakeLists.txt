# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
list(APPEND ZEPHYR_EXTRA_MODULES ${CMAKE_CURRENT_SOURCE_DIR}/../..)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_diagnostics_tests)
target_sources(app PRIVATE src/main.c)
if(XDR_TEST_LONG_GAP)
  # Runs with native_sim accelerated time; never changes the production timer.
  target_compile_definitions(app PRIVATE XDR_TEST_LONG_GAP=1)
endif()

# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20.5)
list(APPEND ZEPHYR_EXTRA_MODULES ${CMAKE_CURRENT_SOURCE_DIR}/../xen-store)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_xenstore_g1)
target_sources(app PRIVATE src/main.c)

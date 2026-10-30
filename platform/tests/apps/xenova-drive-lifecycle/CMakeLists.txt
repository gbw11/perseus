cmake_minimum_required(VERSION 3.20.5)
set(PLATFORM ${CMAKE_CURRENT_SOURCE_DIR}/../../..)
list(APPEND ZEPHYR_EXTRA_MODULES ${PLATFORM}/guests/zephyr/diagnostics
  ${PLATFORM}/guests/zephyr/telemetry ${PLATFORM}/apps/common/xenova-mqtt-c
  ${PLATFORM}/apps/common/xenova-drive-runtime)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_drive_lifecycle)
zephyr_include_directories(${PLATFORM}/guests/zephyr/xen-runtime/include ${PLATFORM}/guests/zephyr/xen-store/include)
# Only this test supplies Xen/Store boundaries on POSIX. Production Kconfig's
# ARM64 requirements remain unchanged; this is not a supported firmware profile.
target_compile_definitions(xenova_drive_runtime PRIVATE CONFIG_XENOVA_RUNTIME=1
  CONFIG_XENOVA_SHUTDOWN_BUDGET_MS=15000 CONFIG_XENOVA_NETFRONT=1)
target_compile_definitions(xenova_telemetry PRIVATE xt_start=xl_real_xt_start)
target_sources(app PRIVATE src/main.c)
if(XL_BASELINE_DRIVE_SOURCE)
  set_property(TARGET xenova_drive_runtime PROPERTY SOURCES ${XL_BASELINE_DRIVE_SOURCE})
  target_sources(app PRIVATE ${XL_BASELINE_DRIVE_ADAPTER})
endif()

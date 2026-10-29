cmake_minimum_required(VERSION 3.20.5)
set(PLATFORM ${CMAKE_CURRENT_SOURCE_DIR}/../../..)
set(DRIVE ${PLATFORM}/apps/drive/zephyr/src)
list(APPEND ZEPHYR_EXTRA_MODULES ${PLATFORM}/guests/zephyr/diagnostics
  ${PLATFORM}/guests/zephyr/telemetry ${PLATFORM}/apps/common/xenova-mqtt-c
  ${PLATFORM}/apps/common/xenova-drive-runtime)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_drive_warm)
target_compile_definitions(app PRIVATE XL_MAIN_SOURCE="${DRIVE}/main.c")
target_include_directories(app PRIVATE ${DRIVE}
  ${PLATFORM}/apps/autonomous/zephyr/src ${PLATFORM}/apps/manual/zephyr/src)
target_sources(app PRIVATE src/main.c ${DRIVE}/workers.c ${DRIVE}/adapter.c ${DRIVE}/controller.c
  ${PLATFORM}/apps/autonomous/zephyr/src/autonomous.c ${PLATFORM}/apps/manual/zephyr/src/manual.c)
# Real main/runtime/adapter/threads; external transport and Publisher only.
foreach(name drive_adapter_run xd_io_start_dual xd_io_snapshot xd_io_report_at xd_io_command xd_io_join xt_start xt_stop xt_status_get exit)
  zephyr_link_libraries(-Wl,--wrap=${name})
endforeach()

cmake_minimum_required(VERSION 3.20.5)
set(PLATFORM ${CMAKE_CURRENT_SOURCE_DIR}/../../..)
set(DRIVE ${PLATFORM}/apps/drive/zephyr/src)
list(APPEND ZEPHYR_EXTRA_MODULES ${PLATFORM}/guests/zephyr/diagnostics
  ${PLATFORM}/guests/zephyr/telemetry ${PLATFORM}/apps/common/xenova-mqtt-c
  ${PLATFORM}/apps/common/xenova-drive-runtime)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_drive_workers)
zephyr_link_libraries(-Wl,--wrap=z_impl_k_thread_create -Wl,--wrap=z_impl_k_mutex_lock
  -Wl,--wrap=drive_adapter_run_until -Wl,--wrap=xdiag_resources_try_get -Wl,--wrap=xdh_assess)
target_include_directories(app PRIVATE ${DRIVE}
  ${PLATFORM}/apps/autonomous/zephyr/src ${PLATFORM}/apps/manual/zephyr/src)
target_sources(app PRIVATE src/main.c ${DRIVE}/workers.c ${DRIVE}/adapter.c
  ${PLATFORM}/apps/autonomous/zephyr/src/autonomous.c
  ${PLATFORM}/apps/manual/zephyr/src/manual.c)

cmake_minimum_required(VERSION 3.20.5)
set(PLATFORM ${CMAKE_CURRENT_SOURCE_DIR}/../../..)
list(APPEND ZEPHYR_EXTRA_MODULES ${PLATFORM}/guests/zephyr/diagnostics
  ${PLATFORM}/guests/zephyr/telemetry ${PLATFORM}/apps/common/xenova-mqtt-c
  ${PLATFORM}/apps/common/xenova-drive-runtime)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_drive_io)
# Compile the product exchange/owner unchanged, with real kernel locks/threads.
# Only Bootstrap, MQTT I/O and capture sink are external boundaries here.
set_property(TARGET xenova_drive_runtime PROPERTY SOURCES ${PLATFORM}/apps/common/xenova-drive-runtime/src/io.c)
if(XL_MQTT_BOUNDARY)
  zephyr_link_libraries(-Wl,--wrap=mqtt_publish,--wrap=mqtt_abort,--wrap=xt_socket_close)
  target_sources(app PRIVATE src/mqtt.c)
else()
  zephyr_link_libraries(-Wl,--wrap=xm_zclient_latest,--wrap=xm_zclient_poll,--wrap=xm_zclient_publish_until,--wrap=xm_zclient_close,--wrap=z_impl_k_thread_create)
  target_sources(app PRIVATE src/main.c)
endif()

cmake_minimum_required(VERSION 3.20.5)
set(PLATFORM ${CMAKE_CURRENT_SOURCE_DIR}/../../..)
list(APPEND ZEPHYR_EXTRA_MODULES ${PLATFORM}/guests/zephyr/diagnostics
  ${PLATFORM}/guests/zephyr/telemetry ${PLATFORM}/apps/common/xenova-mqtt-c
  ${PLATFORM}/apps/common/xenova-drive-runtime)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_drive_io)
# Compile the product exchange/owner unchanged, with real kernel locks/threads.
# Only Bootstrap, MQTT I/O and capture sink are external boundaries here.
if(TARGET xenova_drive_runtime)
  set_property(TARGET xenova_drive_runtime PROPERTY SOURCES ${PLATFORM}/apps/common/xenova-drive-runtime/src/io.c)
endif()
if(XL_INPUT_BOUNDARY)
  set(DRIVE ${PLATFORM}/apps/drive/zephyr/src)
  zephyr_link_libraries(-Wl,--wrap=xm_zclient_poll,--wrap=xm_zclient_close,--wrap=mqtt_readall_publish_payload,--wrap=mqtt_subscribe)
  target_include_directories(app PRIVATE ${DRIVE} ${PLATFORM}/apps/autonomous/zephyr/src ${PLATFORM}/apps/manual/zephyr/src)
  target_sources(app PRIVATE src/receive.c ${DRIVE}/adapter.c
    ${PLATFORM}/apps/autonomous/zephyr/src/autonomous.c ${PLATFORM}/apps/manual/zephyr/src/manual.c)
elseif(XL_NODELAY_BOUNDARY)
  # Real Zephyr TCP socket options; replace only the MQTT connection/peer I/O.
  zephyr_link_libraries(-Wl,--wrap=mqtt_connect,--wrap=mqtt_abort,--wrap=mqtt_live,--wrap=mqtt_publish,--wrap=z_impl_zsock_setsockopt)
  target_sources(app PRIVATE src/nodelay.c)
elseif(XL_CAPTURE_BOUNDARY)
  # Compile the real console/capture callbacks in the test translation unit;
  # only the authenticated-bootstrap setup and MQTT I/O boundary are fixtures.
  zephyr_link_libraries(-Wl,--wrap=xm_zclient_poll,--wrap=xm_zclient_publish_until,--wrap=xm_zclient_close)
  if(XL_CAPTURE_DRIVE_SOURCE)
    target_compile_definitions(app PRIVATE XL_CAPTURE_DRIVE_SOURCE="${XL_CAPTURE_DRIVE_SOURCE}")
  endif()
  target_sources(app PRIVATE src/capture.c)
elseif(XL_MQTT_BOUNDARY)
  zephyr_link_libraries(-Wl,--wrap=mqtt_publish,--wrap=mqtt_abort,--wrap=xt_socket_close)
  target_sources(app PRIVATE src/mqtt.c)
else()
  zephyr_link_libraries(-Wl,--wrap=xm_zclient_latest,--wrap=xm_zclient_poll,--wrap=xm_zclient_publish_until,--wrap=xm_zclient_close,--wrap=z_impl_k_thread_create,--wrap=xm_zclient_subscribe)
  if(XL_FAULT_BOUNDARY)
    # The real command owner/parser is tested; calculation and cached resource
    # evidence are external boundaries, not simulated application failures.
    zephyr_link_libraries(-Wl,--wrap=xm_zclient_publish,--wrap=xdiag_resources_try_get)
  endif()
  target_sources(app PRIVATE src/main.c)
endif()

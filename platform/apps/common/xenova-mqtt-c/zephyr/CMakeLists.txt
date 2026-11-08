# SPDX-License-Identifier: Apache-2.0
# Zephyr 모듈: 호스트 빌드(../CMakeLists.txt)와 같은 코어 소스 + zephyr 포트(net/mqtt 소켓 클라이언트)
if(CONFIG_XENOVA_MQTT)
  set(XM_DIR ${CMAKE_CURRENT_LIST_DIR}/..)
  zephyr_library_named(xenova_mqtt)
  zephyr_library_sources(
    ${XM_DIR}/src/xm_topic.c
    ${XM_DIR}/src/xm_policy.c
    ${XM_DIR}/src/xm_json.c
    ${XM_DIR}/src/xm_envelope.c
    ${XM_DIR}/src/xm_seq.c
    ${XM_DIR}/src/xm_payload.c
    ${XM_DIR}/src/xm_manual.c
    ${XM_DIR}/ports/zephyr/xm_zclient.c
  )
  zephyr_include_directories(${XM_DIR}/include ${XM_DIR}/ports/zephyr)
  zephyr_library_include_directories(${XM_DIR}/src ${XM_DIR}/third_party/jsmn)
endif()

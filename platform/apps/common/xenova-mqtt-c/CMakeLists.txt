# 호스트(Linux) 빌드·시험용. Zephyr 빌드는 zephyr/ 모듈이 같은 소스를 쓴다.
#   cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure
cmake_minimum_required(VERSION 3.20)
project(xenova_mqtt_c C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(XM_CORE_SOURCES
    src/xm_topic.c
    src/xm_policy.c
    src/xm_json.c
    src/xm_envelope.c
    src/xm_seq.c
    src/xm_payload.c
)

add_library(xenova_mqtt_core STATIC ${XM_CORE_SOURCES})
target_include_directories(xenova_mqtt_core PUBLIC include PRIVATE src third_party/jsmn)
target_compile_options(xenova_mqtt_core PRIVATE -Wall -Wextra -Werror -Wno-unused-function)
option(XM_SANITIZE "AddressSanitizer·UBSan 으로 시험" ON)
if(XM_SANITIZE)
  target_compile_options(xenova_mqtt_core PRIVATE -fsanitize=address,undefined)
endif()


enable_testing()
add_executable(test_xm tests/test_xm.c)
target_include_directories(test_xm PRIVATE third_party/jsmn)
target_compile_options(test_xm PRIVATE -Wall -Wextra -fsanitize=address,undefined)
target_link_options(test_xm PRIVATE -fsanitize=address,undefined)
target_link_libraries(test_xm PRIVATE xenova_mqtt_core)
add_test(NAME vectors COMMAND test_xm ${CMAKE_CURRENT_SOURCE_DIR}/tests/vectors)
find_package(Threads REQUIRED)
add_executable(test_payload_threads tests/test_payload_threads.c)
target_compile_options(test_payload_threads PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined)
target_link_options(test_payload_threads PRIVATE -fsanitize=address,undefined)
target_link_libraries(test_payload_threads PRIVATE xenova_mqtt_core Threads::Threads)
add_test(NAME concurrent-payloads COMMAND test_payload_threads)
add_executable(test_state_transfer tests/test_state_transfer.c)
target_include_directories(test_state_transfer PRIVATE ../../drive/zephyr/src ../../autonomous/zephyr/src ../../manual/zephyr/src ../xenova-drive-runtime/include)
target_compile_options(test_state_transfer PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined)
target_link_options(test_state_transfer PRIVATE -fsanitize=address,undefined)
target_link_libraries(test_state_transfer PRIVATE xenova_mqtt_core)
add_test(NAME state-transfer COMMAND test_state_transfer)

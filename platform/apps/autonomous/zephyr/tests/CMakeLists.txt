# 자율주행 로직 호스트 시험 (Zephyr 없이 gcc)
#   cmake -S tests -B build-host && cmake --build build-host && ctest --test-dir build-host --output-on-failure
cmake_minimum_required(VERSION 3.20)
project(xenova_autonomous_host_tests C)

add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../../common/xenova-mqtt-c xenova-mqtt-c)

enable_testing()
add_executable(test_autonomous test_autonomous.c ../src/autonomous.c)
target_include_directories(test_autonomous PRIVATE ../src)
target_compile_options(test_autonomous PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined)
target_link_options(test_autonomous PRIVATE -fsanitize=address,undefined)
target_link_libraries(test_autonomous PRIVATE xenova_mqtt_core m)
add_test(NAME autonomous COMMAND test_autonomous)

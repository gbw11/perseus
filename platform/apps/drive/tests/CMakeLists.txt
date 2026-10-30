cmake_minimum_required(VERSION 3.20)
project(xenova_drive_controller_tests C)
enable_testing()
add_subdirectory(../../common/xenova-mqtt-c mqtt-core)
add_executable(test_controller controller.c ../zephyr/src/controller.c ../zephyr/src/adapter.c
  ../../../guests/zephyr/diagnostics/src/health.c
  ../../autonomous/zephyr/src/autonomous.c ../../manual/zephyr/src/manual.c)
target_include_directories(test_controller PRIVATE ../zephyr/src
  ../../../guests/zephyr/diagnostics/include
  ../../autonomous/zephyr/src ../../manual/zephyr/src)
target_compile_options(test_controller PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined)
target_link_options(test_controller PRIVATE -fsanitize=address,undefined)
target_link_libraries(test_controller PRIVATE xenova_mqtt_core m)
add_test(NAME common-controller COMMAND test_controller)
add_executable(test_health ../../../guests/zephyr/diagnostics/tests/test_health.c
 ../../../guests/zephyr/diagnostics/src/health.c)
target_include_directories(test_health PRIVATE ../../../guests/zephyr/diagnostics/include)
target_compile_options(test_health PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined)
target_link_options(test_health PRIVATE -fsanitize=address,undefined)
add_test(NAME health-evidence COMMAND test_health)

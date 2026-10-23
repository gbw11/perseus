# SPDX-License-Identifier: Apache-2.0
if(CONFIG_XENOVA_RUNTIME)
 zephyr_library_named(xenova_runtime)
 zephyr_include_directories(include)
 zephyr_library_sources(src/runtime.c src/shutdown.c src/shutdown_flow.c)
 zephyr_library_sources_ifdef(CONFIG_XENOVA_NETFRONT src/grants.c src/grant_pool.c src/netfront_ring.c src/netfront.c)
endif()

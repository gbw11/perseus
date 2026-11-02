# Production lifecycle sources with only Xen/Store/net-device boundaries replaced.
cmake_minimum_required(VERSION 3.20.5)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xenova_lifecycle)
zephyr_link_libraries(-Wl,--wrap=xnf_poll,--wrap=z_impl_k_thread_create)
set(BASE ${CMAKE_CURRENT_SOURCE_DIR}/../../../guests/zephyr)
target_include_directories(app BEFORE PRIVATE boundaries ${BASE}/xen-runtime/include ${BASE}/xen-store/include)
target_compile_definitions(app PRIVATE CONFIG_XENOVA_NETFRONT=1
  CONFIG_XENOVA_NETFRONT_THREAD_PRIORITY=7 CONFIG_XENOVA_SHUTDOWN_THREAD_PRIORITY=6
  CONFIG_XENOVA_SHUTDOWN_BUDGET_MS=15000)
target_sources(app PRIVATE src/boundaries.c
  ${BASE}/xen-runtime/src/netfront.c ${BASE}/xen-runtime/src/netfront_ring.c
  ${BASE}/xen-runtime/src/shutdown.c ${BASE}/xen-runtime/src/shutdown_flow.c
  ${BASE}/xen-runtime/src/grant_pool.c)
if(XL_NETFRONT_POLICY)
  target_sources(app PRIVATE src/net_policy.c)
elseif(XL_WAIT_TEST)
  target_sources(app PRIVATE src/wait.c)
elseif(XL_STARTER)
  target_compile_definitions(app PRIVATE CONFIG_XENOVA_DIAGNOSTICS_EXERCISE=1)
  set_source_files_properties(${BASE}/network-starter-app/src/main.c PROPERTIES COMPILE_DEFINITIONS main=xl_starter_main)
  target_sources(app PRIVATE src/starter.c ${BASE}/network-starter-app/src/main.c ${BASE}/network-starter-app/src/exercise.c)
elseif(XL_BASELINE_NET_SOURCE)
  # The task runner supplies an immutable git-show source outside the repo.
  set_property(SOURCE ${BASE}/xen-runtime/src/netfront.c PROPERTY HEADER_FILE_ONLY TRUE)
  target_sources(app PRIVATE ${XL_BASELINE_NET_SOURCE} ${XL_BASELINE_NET_MAIN})
else()
  target_sources(app PRIVATE src/main.c)
endif()

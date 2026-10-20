# SPDX-License-Identifier: Apache-2.0
if(CONFIG_XENOVA_XENSTORE)
  zephyr_library_named(xenova_xenstore)
  zephyr_library_sources(src/xenstore.c src/xenbus_vif.c src/xenstore_link.c src/xenstore_zephyr.c)
  zephyr_include_directories(include)
endif()

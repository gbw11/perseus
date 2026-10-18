These minimized fixtures are derived from the pinned Xen 4.21+stable source,
not captured from a running board.

tools/libs/light/gentypes.py:get_default_expr omits an Array member when
its pointer is null or its length is zero. Both JSON serializers use this
condition. diskless-omitted.json represents that output. diskless-empty.json
also exercises the explicit empty-array representation.

tools/xl/xl_info.c:list_domains_details skips the entire domain if
libxl_retrieve_domain_configuration fails. The short/long domid comparison
must still reject that case.

The short-list state columns come from xl_info.c:list_domains:
running, blocked, paused, shutdown, shutdown_reason ("-rscwS"), dying.
The fifth character is a reason, not an independent crashed bit.

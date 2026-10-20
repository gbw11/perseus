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

Reset fixture: Xen stable-4.21 SRCREV 1c72306b1f0b4dc533954f678361044f8083dccc,
arch/arm/platforms/brcm-raspberry-pi.c before the acceptance patch.
SHA256 8b264872ddd5ad17f08b554aef5bbefdfc5598f603367aeb2bf12f1998a72c96. Original GPL-2.0-or-later notice retained.
The native stub test checks DT matching/MMIO calls only, never hardware reset.

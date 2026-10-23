# Xenova network Starter runtime

One XenStore receiver/session, one VIF and one TX/RX queue pair per domain.
G1 wire client, permissions, transaction retries and pending-event replay are
reused. Store users acquire a bounded mutex and never retain the client.
Watch consumers reread values; no application callback runs under that mutex.

Ethernet uses Zephyr net_if and standard IPv4/ARP/ICMP/DHCP. MTU 1500, RX-copy,
8 packet pages per direction, 256 ABI ring entries. IRQ only wakes a thread.
TX pages are held until matching completion; descriptor generations, indices,
length and flags are checked. No checksum offload, extra-info, scatter/gather,
GSO, jumbo, IPv6 or live hotplug reconnect is claimed.

The profile disables upstream XEN_GRANT_TABLE and owns one grant-v1 frame with
32 bounded entries above reserved references 0..7. Static pinned pages use the
frozen permanent-RAM translation helper, never pointer-cast GFNs. Busy revoke
never frees a reference or reuses its page. This avoids the frozen upstream
driver's inverted end-access/free-list path without patching upstream.
The netfront profile must be the sole grant table owner.
Store/shutdown alone can coexist with an existing grant driver.

A separate thread watches control/shutdown and rereads after registration.
Empty ACK means receipt. App hooks must honor the local uptime deadline and
return only after final-state acknowledgement. The network worker stays alive
through the hook. Drain, XenBus Closing/Closed and owned grant cleanup precede
Store close and SCHEDOP_shutdown. A returning hypercall is unconfirmed, even 0.
No output rearm, force escalation or vehicle readiness is provided.

Native tests compile the production ring, grant pool and shutdown flow.
They do not prove actual Xen packets or board shutdown. Pinned Xen public MIT
ABI headers are in references. Linux netback is inspected only, not copied.
Zephyr/SDK retain the original source.lock.json revision.

Thread priorities and teardown budgets are configurable provisional application
settings. They are not host RT scheduling or vehicle safety/timing thresholds.

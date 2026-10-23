# Network Starter 1.1.0 (development candidate)

Diskless 1-vCPU / 16-MiB Xen guest, with Ethernet/IPv4/ARP/ICMP/DHCP and a
reusable cooperative shutdown hook. The old Starter and G1 app are preserved.

PV console commands: net iface, net ping <IPv4>, xenova stats.
The demo hook has no driving state or physical output and says so explicitly.
xenova hook normal|reject|fail|timeout controls only this development hook.
A shutdown failure is latched until a new execution; no output is rearmed.

The xenvm board has no entropy device. Zephyr's timer test RNG is explicitly
limited to DHCP/ping identifiers and UDP source selection. It is not an
authentication, secret, cryptographic identity or vehicle authority source.
Applications needing cryptography must supply a reviewed entropy source.

Partial target build/native tests are separate from actual board acceptance.
See platform/docs/resource-boot-zephyr.md. Do not publish this candidate as a
board-accepted release before actual A/B tests.

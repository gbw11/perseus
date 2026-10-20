# The bring-up image uses a separate systemd unit and explicitly bound DHCP.
# Prevent dnsmasq's distribution default unit from unintentionally listening
# on real interfaces. Other images are unaffected unless they install dnsmasq.
SYSTEMD_AUTO_ENABLE:${PN} = "disable"

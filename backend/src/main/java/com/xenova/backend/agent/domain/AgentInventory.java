package com.xenova.backend.agent.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Convert;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;

// Latest OS report from the agent inside a VM (MQTT-02 inventory). One row per VM.
@Entity
@Table(name = "agent_inventories")
public class AgentInventory {

    private static final Pattern IPV4 = Pattern.compile("^(\\d{1,3})(\\.\\d{1,3}){3}$");

    @Id
    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Column(name = "boot_id", length = 64)
    private String bootId;

    @Column(name = "os_id", length = 64)
    private String osId;

    @Column(name = "os_version_id", length = 64)
    private String osVersionId;

    @Column(name = "pretty_name", length = 255)
    private String prettyName;

    @Column(name = "kernel_release", length = 128)
    private String kernelRelease;

    @Column(length = 255)
    private String hostname;

    @Lob
    @Convert(converter = NetworkInterfacesConverter.class)
    @Column(name = "interfaces", columnDefinition = "longtext")
    private List<NetworkInterface> interfaces;

    @Column(name = "agent_version", length = 64)
    private String agentVersion;

    @Column(name = "observed_at")
    private Instant observedAt;

    @Column(name = "received_at", nullable = false)
    private Instant receivedAt;

    protected AgentInventory() {
    }

    public AgentInventory(String vmId) {
        this.vmId = vmId;
    }

    // A report older than the stored one (e.g. a late retained message) must not overwrite it.
    public boolean isSupersededBy(Instant reportObservedAt) {
        return observedAt == null || reportObservedAt == null || !reportObservedAt.isBefore(observedAt);
    }

    public void update(InventoryReport report, Instant receivedAt) {
        this.bootId = report.bootId();
        this.osId = report.osId();
        this.osVersionId = report.osVersionId();
        this.prettyName = report.prettyName();
        this.kernelRelease = report.kernelRelease();
        this.hostname = report.hostname();
        this.interfaces = report.interfaces();
        this.agentVersion = report.agentVersion();
        this.observedAt = report.observedAt();
        this.receivedAt = receivedAt;
    }

    public String getVmId() {
        return vmId;
    }

    public String getBootId() {
        return bootId;
    }

    public String getOsId() {
        return osId;
    }

    public String getOsVersionId() {
        return osVersionId;
    }

    public String getPrettyName() {
        return prettyName;
    }

    public String getKernelRelease() {
        return kernelRelease;
    }

    public String getHostname() {
        return hostname;
    }

    public List<NetworkInterface> getInterfaces() {
        return interfaces == null ? List.of() : interfaces;
    }

    // The address shown for the VM: the first IPv4 the agent reported, eth0 first, loopback skipped.
    public Optional<String> primaryIpv4() {
        return getInterfaces().stream()
                .sorted(Comparator.comparing((NetworkInterface nic) -> !"eth0".equals(nic.name())))
                .flatMap(nic -> nic.ips().stream())
                .filter(ip -> IPV4.matcher(ip).matches() && !ip.startsWith("127."))
                .findFirst();
    }

    public String getAgentVersion() {
        return agentVersion;
    }

    public Instant getObservedAt() {
        return observedAt;
    }

    public Instant getReceivedAt() {
        return receivedAt;
    }
}

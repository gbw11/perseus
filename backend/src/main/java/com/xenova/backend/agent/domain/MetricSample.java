package com.xenova.backend.agent.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Convert;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.List;

// One metrics report from a VM (MQTT-03). The message_id becomes sample_id so redelivered messages are dropped.
@Entity
@Table(name = "vm_metric_samples",
        indexes = @Index(name = "idx_vm_metric_samples_vm_observed", columnList = "vm_id, observed_at"))
public class MetricSample {

    public static final String ZEPHYR_TELEMETRY = "zephyr_telemetry";
    public static final String LINUX_TELEMETRY = "linux_telemetry";
    public static final String VM_TELEMETRY = "vm_telemetry";

    @Id
    @Column(name = "sample_id", length = 36)
    private String sampleId;

    @Column(name = "vm_id", nullable = false, length = 36)
    private String vmId;

    @Column(name = "boot_id", length = 64)
    private String bootId;

    @Column(nullable = false, length = 16)
    private String source;

    @Column(name = "observed_at", nullable = false)
    private Instant observedAt;

    @Column(name = "received_at", nullable = false)
    private Instant receivedAt;

    @Column(name = "window_ms")
    private Integer windowMs;

    @Lob
    @Convert(converter = MetricValuesConverter.class)
    @Column(name = "metric_values", columnDefinition = "longtext")
    private MetricSampleData data;

    protected MetricSample() {
    }

    public MetricSample(
            String sampleId,
            String vmId,
            String bootId,
            String source,
            Instant observedAt,
            Instant receivedAt,
            Integer windowMs,
            List<MetricValue> values) {
        this(sampleId, vmId, bootId, source, observedAt, receivedAt, windowMs,
                new MetricSampleData(values, null, null));
    }

    public MetricSample(
            String sampleId, String vmId, String bootId, String source, Instant observedAt,
            Instant receivedAt, Integer windowMs, MetricSampleData data) {
        this.sampleId = sampleId;
        this.vmId = vmId;
        this.bootId = bootId;
        this.source = source;
        this.observedAt = observedAt;
        this.receivedAt = receivedAt;
        this.windowMs = windowMs;
        this.data = data;
    }

    public String getSampleId() {
        return sampleId;
    }

    public String getVmId() {
        return vmId;
    }

    public String getBootId() {
        return bootId;
    }

    public String getSource() {
        return source;
    }

    public Instant getObservedAt() {
        return observedAt;
    }

    public Instant getReceivedAt() {
        return receivedAt;
    }

    public Integer getWindowMs() {
        return windowMs;
    }

    public List<MetricValue> getValues() {
        return data == null ? List.of() : data.values();
    }

    public MetricSampleData.NetworkObservation getNetwork() {
        return data == null ? null : data.network();
    }

    public MetricSampleData.CpuWindow getCpuWindow() {
        return data == null ? null : data.cpuWindow();
    }

    public MetricSampleData.TelemetryObservation getTelemetry() {
        return data == null ? null : data.telemetry();
    }
}

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
    @Column(name = "metric_values")
    private List<MetricValue> values;

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
        this.sampleId = sampleId;
        this.vmId = vmId;
        this.bootId = bootId;
        this.source = source;
        this.observedAt = observedAt;
        this.receivedAt = receivedAt;
        this.windowMs = windowMs;
        this.values = values;
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
        return values == null ? List.of() : values;
    }
}

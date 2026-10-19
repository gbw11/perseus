package com.perseus.backend.event.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Convert;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.Map;

// Something that happened to a VM or its agent, created by Spring itself (not received over MQTT).
// The vm_id is kept as plain text so the history of a deleted VM stays readable.
@Entity
@Table(name = "vm_events", indexes = {
        @Index(name = "idx_vm_events_vm_id", columnList = "vm_id"),
        @Index(name = "idx_vm_events_recorded_at", columnList = "recorded_at")})
public class VmEvent {

    // Insertion order. after_event_id resumes from it, because recorded_at can repeat.
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "event_id", nullable = false, unique = true, length = 36)
    private String eventId;

    @Enumerated(EnumType.STRING)
    @Column(name = "type", nullable = false, length = 40)
    private EventType type;

    @Enumerated(EnumType.STRING)
    @Column(name = "severity", nullable = false, length = 10)
    private EventSeverity severity;

    @Column(name = "vm_id", length = 36)
    private String vmId;

    // Reserved: VM operations are synchronous for now, so no operation id exists yet.
    @Column(name = "operation_id", length = 36)
    private String operationId;

    @Column(name = "occurred_at", nullable = false)
    private Instant occurredAt;

    @Column(name = "recorded_at", nullable = false)
    private Instant recordedAt;

    @Convert(converter = EventPayloadConverter.class)
    @Column(name = "payload", nullable = false, length = 4000)
    private Map<String, Object> payload;

    protected VmEvent() {
    }

    public VmEvent(
            String eventId,
            EventType type,
            EventSeverity severity,
            String vmId,
            Instant occurredAt,
            Instant recordedAt,
            Map<String, Object> payload) {
        this.eventId = eventId;
        this.type = type;
        this.severity = severity;
        this.vmId = vmId;
        this.occurredAt = occurredAt;
        this.recordedAt = recordedAt;
        this.payload = payload;
    }

    public Long getId() {
        return id;
    }

    public String getEventId() {
        return eventId;
    }

    public EventType getType() {
        return type;
    }

    public EventSeverity getSeverity() {
        return severity;
    }

    public String getVmId() {
        return vmId;
    }

    public String getOperationId() {
        return operationId;
    }

    public Instant getOccurredAt() {
        return occurredAt;
    }

    public Instant getRecordedAt() {
        return recordedAt;
    }

    public Map<String, Object> getPayload() {
        return payload;
    }
}

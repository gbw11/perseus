package com.perseus.backend.event.application;

import com.perseus.backend.event.domain.EventSeverity;
import com.perseus.backend.event.domain.EventType;
import com.perseus.backend.event.domain.VmEvent;
import com.perseus.backend.event.domain.VmEventRepository;
import com.perseus.backend.vm.domain.VmRecord;
import com.perseus.backend.vm.domain.VmState;
import java.time.Clock;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

// Stores events and announces them to WS-02. Payloads never carry passwords, tokens or terminal input.
@Service
public class EventRecorder {

    private final VmEventRepository repository;
    private final ApplicationEventPublisher eventPublisher;
    private final Clock clock;

    public EventRecorder(VmEventRepository repository, ApplicationEventPublisher eventPublisher, Clock clock) {
        this.repository = repository;
        this.eventPublisher = eventPublisher;
        this.clock = clock;
    }

    @Transactional
    public VmEvent record(
            EventType type, EventSeverity severity, String vmId, Instant occurredAt, Map<String, Object> payload) {
        Instant now = clock.instant();
        VmEvent event = repository.save(new VmEvent(
                UUID.randomUUID().toString(), type, severity, vmId, occurredAt == null ? now : occurredAt, now, payload));
        eventPublisher.publishEvent(new EventRecorded(event));
        return event;
    }

    public void vmCreated(VmRecord record) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("name", record.getName());
        payload.put("template_id", record.getTemplateId());
        payload.put("template_version", record.getTemplateVersion());
        record(EventType.VM_CREATED, EventSeverity.INFO, record.getVmId(), null, payload);
    }

    public void vmDeleted(String vmId, String name) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("name", name);
        record(EventType.VM_DELETED, EventSeverity.INFO, vmId, null, payload);
    }

    // from is null when the previous state was not looked up (e.g. a stop request).
    public void vmStateChanged(String vmId, VmState from, VmState to, String cause) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("from", from == null ? null : from.value());
        payload.put("to", to.value());
        payload.put("cause", cause);
        EventSeverity severity = to == VmState.CRASHED ? EventSeverity.ERROR : EventSeverity.INFO;
        record(EventType.VM_STATE_CHANGED, severity, vmId, null, payload);
    }
}

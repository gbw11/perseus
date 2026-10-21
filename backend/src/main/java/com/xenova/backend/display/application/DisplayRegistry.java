package com.xenova.backend.display.application;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.event.application.EventRecorder;
import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.time.Clock;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

// Screen routing for the two monitors (REST-23·24, S15P31E110-129). There is no dom0 display service yet, so the
// monitor browser (kiosk) itself connects to WS /ws/v1/displays, follows desired_route and confirms with
// route.applied. State lives in memory only: a restart puts both monitors back on the split view.
@Service
public class DisplayRegistry {

    public static final String SPLIT = "split";
    public static final String OS_FACE = "os-face";
    public static final List<String> SUPPORTED_VIEWS = List.of(SPLIT, OS_FACE);
    static final String BROWSER_READY = "browser_ready";

    private static final Logger log = LoggerFactory.getLogger(DisplayRegistry.class);

    private final VmRecordRepository vmRecordRepository;
    private final EventRecorder eventRecorder;
    private final ApplicationEventPublisher eventPublisher;
    private final Clock clock;
    private final Map<String, Slot> slots = new LinkedHashMap<>();

    public DisplayRegistry(
            VmRecordRepository vmRecordRepository, EventRecorder eventRecorder,
            ApplicationEventPublisher eventPublisher, Clock clock) {
        this.vmRecordRepository = vmRecordRepository;
        this.eventRecorder = eventRecorder;
        this.eventPublisher = eventPublisher;
        this.clock = clock;
        slots.put("display-0", new Slot("display-0", "모니터 1"));
        slots.put("display-1", new Slot("display-1", "모니터 2"));
    }

    /** Thrown for WS frames; REST maps the same cases to ApiException. */
    public static final class RouteRejected extends RuntimeException {
        private final String code;

        RouteRejected(String code, String message) {
            super(message);
            this.code = code;
        }

        public String code() {
            return code;
        }
    }

    public synchronized List<Display> list() {
        return slots.values().stream().map(Slot::view).toList();
    }

    public synchronized boolean exists(String displayId) {
        return displayId != null && slots.containsKey(displayId);
    }

    // PUT /api/v1/displays/{id}/route. Order of checks: display, view and vm_id shape, VM, revision.
    public Display changeRoute(String displayId, String vmId, String view, Long expectedRevision, String requestedBy) {
        Display changed;
        synchronized (this) {
            Slot slot = slot(displayId);
            String target = vmId == null || vmId.isBlank() ? null : vmId;
            if (view == null || view.isBlank()) {
                throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "view 는 필수입니다.");
            }
            if (!SUPPORTED_VIEWS.contains(view)) {
                throw new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "UNSUPPORTED_VIEW", "지원하지 않는 view 입니다: " + view);
            }
            if (expectedRevision == null) {
                throw new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "expected_revision 은 필수입니다.");
            }
            if (SPLIT.equals(view) && target != null) {
                throw new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "split 화면에는 vm_id 를 지정할 수 없습니다.");
            }
            if (OS_FACE.equals(view) && target == null) {
                throw new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "os-face 화면에는 vm_id 가 필요합니다.");
            }
            if (target != null && vmRecordRepository.findById(target).filter(record -> record.isRegistered()).isEmpty()) {
                throw new ApiException(HttpStatus.NOT_FOUND, "VM_NOT_FOUND", "존재하지 않는 VM입니다.");
            }
            if (expectedRevision != slot.revision) {
                throw new ApiException(HttpStatus.CONFLICT, "DISPLAY_REVISION_CONFLICT",
                        "다른 요청이 먼저 화면을 바꿨습니다. 현재 revision: " + slot.revision);
            }
            slot.revision++;
            slot.desired = new Display.Route(target, view, UUID.randomUUID().toString(), null, null);
            changed = slot.view();
        }
        recordChange(changed, requestedBy);
        changed();
        return changed;
    }

    // route.applied from the monitor. An older revision is ignored (the console moved on meanwhile).
    public void applied(String displayId, Long revision) {
        synchronized (this) {
            Slot slot = slots.get(displayId);
            if (slot == null) {
                throw new RouteRejected("DISPLAY_NOT_FOUND", "존재하지 않는 디스플레이입니다.");
            }
            if (revision == null || revision > slot.revision) {
                throw new RouteRejected("VALIDATION_ERROR", "revision 이 올바르지 않습니다.");
            }
            if (revision < slot.revision) {
                throw new RouteRejected("DISPLAY_REVISION_CONFLICT", "이미 새 배치가 있습니다. revision: " + slot.revision);
            }
            Display.Route desired = slot.desired;
            slot.observed = new Display.Route(
                    desired.vmId(), desired.view(), desired.requestId(), clock.instant(), BROWSER_READY);
            slot.observedRevision = revision;
        }
        changed();
    }

    public void connected(String displayId) {
        synchronized (this) {
            slot(displayId).connections++;
        }
        changed();
    }

    // When the last monitor socket closes nobody knows what is on screen, so observed_route is cleared.
    public void disconnected(String displayId) {
        synchronized (this) {
            Slot slot = slots.get(displayId);
            if (slot == null || slot.connections == 0) {
                return;
            }
            slot.connections--;
            if (slot.connections == 0) {
                slot.observed = null;
                slot.observedRevision = -1;
            }
        }
        changed();
    }

    private Slot slot(String displayId) {
        Slot slot = displayId == null ? null : slots.get(displayId);
        if (slot == null) {
            throw new ApiException(HttpStatus.NOT_FOUND, "DISPLAY_NOT_FOUND", "존재하지 않는 디스플레이입니다.");
        }
        return slot;
    }

    private void changed() {
        eventPublisher.publishEvent(new DisplaysChanged(list()));
    }

    private void recordChange(Display display, String requestedBy) {
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("display_id", display.displayId());
            payload.put("view", display.desiredRoute().view());
            payload.put("vm_id", display.desiredRoute().vmId());
            payload.put("revision", display.revision());
            payload.put("request_id", display.desiredRoute().requestId());
            payload.put("requested_by", requestedBy);
            eventRecorder.record(
                    EventType.DISPLAY_ROUTE_CHANGED, EventSeverity.INFO, display.desiredRoute().vmId(), null, payload);
        } catch (RuntimeException exception) {
            log.error("Failed to record display route change for {}", display.displayId(), exception);
        }
    }

    private static final class Slot {
        private final String displayId;
        private final String label;
        private long revision = 1;
        private Display.Route desired = new Display.Route(null, SPLIT, "initial", null, null);
        private Display.Route observed;
        private long observedRevision = -1;
        private int connections;

        private Slot(String displayId, String label) {
            this.displayId = displayId;
            this.label = label;
        }

        private Display view() {
            DisplayStatus status = connections == 0 ? DisplayStatus.DISCONNECTED
                    : observedRevision == revision ? DisplayStatus.ACTIVE : DisplayStatus.SWITCHING;
            return new Display(displayId, label, connections > 0, status, SUPPORTED_VIEWS, revision, desired,
                    observed, null);
        }
    }

    // Published after every change; the display socket pushes a snapshot to all subscribers.
    public record DisplaysChanged(List<Display> items) {
    }
}

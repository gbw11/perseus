package com.xenova.backend.display.application;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.event.application.EventRecorder;
import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import com.xenova.backend.vm.domain.VmRole;
import java.time.Clock;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

// Screen routing for the two monitors (REST-23·24, S15P31E110-129). The dom0 display service connects to
// WS /ws/v1/display-service, receives route.set for every desired_route and answers route.applied / route.failed.
// Monitor browsers do not talk to the backend. State lives in memory only: a restart puts both monitors back on
// the split view.
@Service
public class DisplayRegistry {

    public static final String SPLIT = "split";
    public static final String OS_FACE = "os-face";
    public static final List<String> SUPPORTED_VIEWS = List.of(SPLIT, OS_FACE);
    static final String KIOSK_LOADED = "kiosk_loaded";
    // request_id of the start-up split route (contract display-route-v1 §2).
    static final String INITIAL_REQUEST = "initial";

    private static final Logger log = LoggerFactory.getLogger(DisplayRegistry.class);

    private final VmRecordRepository vmRecordRepository;
    private final EventRecorder eventRecorder;
    private final ApplicationEventPublisher eventPublisher;
    private final Clock clock;
    private final Map<String, Slot> slots = new LinkedHashMap<>();
    // True between service.hello and the service socket closing.
    private boolean serviceConnected;

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

    /** One display in service.hello. */
    public record DisplayLink(String displayId, boolean connected) {
    }

    /** What route.set carries. vm_id and app_id are null for split. */
    public record RouteSet(
            String displayId, long revision, String requestId, String view, String vmId, String appId,
            String screenPath) {
    }

    public synchronized List<Display> list() {
        return slots.values().stream().map(slot -> slot.view(serviceConnected)).toList();
    }

    public synchronized List<String> displayIds() {
        return List.copyOf(slots.keySet());
    }

    // PUT /api/v1/displays/{id}/route. Order of checks: display, view and vm_id shape, VM, VM role, revision.
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
            VmRole role = null;
            if (target != null) {
                VmRecord record = vmRecordRepository.findById(target).filter(VmRecord::isRegistered)
                        .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "VM_NOT_FOUND", "존재하지 않는 VM입니다."));
                role = record.getRole();
                if (role == null) {
                    throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "VM_ROLE_UNKNOWN",
                            "VM 역할(role)이 없어 전용 화면을 정할 수 없습니다.");
                }
            }
            if (expectedRevision != slot.revision) {
                throw new ApiException(HttpStatus.CONFLICT, "DISPLAY_REVISION_CONFLICT",
                        "다른 요청이 먼저 화면을 바꿨습니다. 현재 revision: " + slot.revision);
            }
            slot.revision++;
            slot.desired = new Display.Route(target, view, UUID.randomUUID().toString(), null, null);
            slot.role = role;
            changed = slot.view(serviceConnected);
        }
        recordChange(changed, requestedBy);
        changed();
        eventPublisher.publishEvent(new RouteChanged(displayId));
        return changed;
    }

    // service.hello: displays not listed count as unplugged. Nothing is known to be on screen yet, so every
    // connected display is IDLE until its route.set goes out.
    public void serviceConnected(List<DisplayLink> displays) {
        synchronized (this) {
            serviceConnected = true;
            for (Slot slot : slots.values()) {
                slot.reset();
            }
            for (DisplayLink link : displays) {
                Slot slot = slots.get(link.displayId());
                if (slot != null) {
                    slot.connected = link.connected();
                }
            }
        }
        changed();
    }

    // The service socket closed (or was replaced): every display is DISCONNECTED until the next hello.
    public void serviceDisconnected() {
        synchronized (this) {
            if (!serviceConnected) {
                return;
            }
            serviceConnected = false;
            for (Slot slot : slots.values()) {
                slot.reset();
            }
        }
        changed();
    }

    // display.status: the monitor or its kiosk browser came or went. desired_route and revision stay; the service
    // already holds the latest route.set and answers route.applied once the kiosk shows it again.
    public void displayStatus(String displayId, boolean connected) {
        synchronized (this) {
            Slot slot = known(displayId);
            if (slot.connected == connected) {
                return;
            }
            slot.connected = connected;
            if (!connected) {
                // Whatever the kiosk showed is gone with the monitor; route.applied has to confirm it again.
                slot.observed = null;
                slot.observedRevision = -1;
                slot.failedRevision = -1;
                slot.lastError = null;
            }
        }
        changed();
    }

    /** The command for the current desired_route, or empty while no service has said hello. */
    public synchronized Optional<RouteSet> routeSet(String displayId) {
        Slot slot = slots.get(displayId);
        if (slot == null || !serviceConnected) {
            return Optional.empty();
        }
        return Optional.of(slot.routeSet());
    }

    // Called once route.set for that revision is on the wire (IDLE → SWITCHING).
    public void routeSent(String displayId, long revision) {
        synchronized (this) {
            Slot slot = slots.get(displayId);
            if (slot == null || !serviceConnected || revision <= slot.sentRevision) {
                return;
            }
            slot.sentRevision = revision;
        }
        changed();
    }

    // route.applied: the kiosk finished loading that screen. An older revision is ignored (the console moved on).
    public void applied(String displayId, Long revision, String requestId) {
        synchronized (this) {
            Slot slot = current(displayId, revision, requestId);
            if (slot == null) {
                return;
            }
            Display.Route desired = slot.desired;
            slot.observed = new Display.Route(
                    desired.vmId(), desired.view(), desired.requestId(), clock.instant(), KIOSK_LOADED);
            slot.observedRevision = revision;
            slot.failedRevision = -1;
            slot.lastError = null;
        }
        changed();
    }

    // route.failed: the kiosk could not show the current revision. observed_route keeps the last good screen.
    public void failed(String displayId, Long revision, String requestId, String reason) {
        synchronized (this) {
            Slot slot = current(displayId, revision, requestId);
            if (slot == null) {
                return;
            }
            slot.failedRevision = revision;
            slot.lastError = reason == null || reason.isBlank() ? "unknown" : reason;
        }
        changed();
    }

    // The slot a route result is about, or null for an older revision (dropped without an answer). A future
    // revision or another request_id is an error. A missing request_id is accepted (revision alone decides).
    private Slot current(String displayId, Long revision, String requestId) {
        Slot slot = known(displayId);
        if (revision == null || revision > slot.revision) {
            throw new RouteRejected("VALIDATION_ERROR", "revision 이 올바르지 않습니다. 현재 revision: " + slot.revision);
        }
        if (revision < slot.revision) {
            return null;
        }
        if (requestId != null && !requestId.equals(slot.desired.requestId())) {
            throw new RouteRejected("VALIDATION_ERROR", "request_id 가 현재 배치와 다릅니다.");
        }
        return slot;
    }

    private Slot known(String displayId) {
        Slot slot = displayId == null ? null : slots.get(displayId);
        if (slot == null) {
            throw new RouteRejected("DISPLAY_NOT_FOUND", "존재하지 않는 디스플레이입니다.");
        }
        return slot;
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

    // Legacy Spring display route. The role is an explicit UUID binding; never infer A/B ownership from
    // autonomous/manual app ids. The dom0 display pipeline uses its own /display/* routes.
    static String screenPath(String view, VmRole role) {
        if (!OS_FACE.equals(view) || role == null) {
            return "/split";
        }
        return switch (role) {
            case PRIMARY_DRIVE -> "/detail/primary_drive";
            case BACKUP_DRIVE -> "/detail/backup_drive";
            case INTERACTION -> "/detail/interaction";
        };
    }

    private static final class Slot {
        private final String displayId;
        private final String label;
        private long revision = 1;
        private Display.Route desired = new Display.Route(null, SPLIT, INITIAL_REQUEST, null, null);
        // Role of desired.vmId at PUT time; decides app_id and screen_path of route.set.
        private VmRole role;
        private Display.Route observed;
        private long observedRevision = -1;
        private long failedRevision = -1;
        private long sentRevision = -1;
        private String lastError;
        // HDMI and kiosk browser up, as the service last reported.
        private boolean connected;

        private Slot(String displayId, String label) {
            this.displayId = displayId;
            this.label = label;
        }

        // Forget everything the service told us; desired_route and revision stay.
        private void reset() {
            connected = false;
            observed = null;
            observedRevision = -1;
            failedRevision = -1;
            sentRevision = -1;
            lastError = null;
        }

        private RouteSet routeSet() {
            return new RouteSet(displayId, revision, desired.requestId(), desired.view(), desired.vmId(),
                    role == null ? null : role.value(), screenPath(desired.view(), role));
        }

        // Contract display-route-v1 §5.4: the first matching row wins.
        private Display view(boolean serviceConnected) {
            boolean up = serviceConnected && connected;
            DisplayStatus status;
            if (!up) {
                status = DisplayStatus.DISCONNECTED;
            } else if (sentRevision < revision) {
                status = DisplayStatus.IDLE;
            } else if (failedRevision == revision) {
                status = DisplayStatus.ERROR;
            } else if (observedRevision == revision) {
                status = DisplayStatus.ACTIVE;
            } else {
                status = DisplayStatus.SWITCHING;
            }
            return new Display(displayId, label, up, status, SUPPORTED_VIEWS, revision, desired,
                    up ? observed : null, status == DisplayStatus.ERROR ? lastError : null);
        }
    }

    // Published after every change; the console socket pushes a snapshot to all subscribers.
    public record DisplaysChanged(List<Display> items) {
    }

    // Published after a PUT; the service socket sends route.set for the display's current desired_route.
    public record RouteChanged(String displayId) {
    }
}

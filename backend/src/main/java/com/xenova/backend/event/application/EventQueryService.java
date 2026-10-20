package com.xenova.backend.event.application;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import com.xenova.backend.event.domain.VmEventRepository;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class EventQueryService {

    public static final int DEFAULT_LIMIT = 100;
    public static final int MAX_LIMIT = 500;

    private final VmEventRepository repository;

    public EventQueryService(VmEventRepository repository) {
        this.repository = repository;
    }

    // Newest first; with after_event_id the events after it come oldest first, to refill a gap in order.
    @Transactional(readOnly = true)
    public List<VmEvent> find(EventQuery query) {
        if (query.limit() < 1 || query.limit() > MAX_LIMIT) {
            throw new ApiException(
                    HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "limit은 1~" + MAX_LIMIT + "이어야 합니다.");
        }
        if (query.from() != null && query.to() != null && !query.from().isBefore(query.to())) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "TIME_RANGE_INVALID", "from은 to보다 앞서야 합니다.");
        }
        Long afterId = null;
        if (query.afterEventId() != null) {
            afterId = cursor(query.afterEventId()).orElseThrow(() -> new ApiException(
                    HttpStatus.CONFLICT, "EVENT_CURSOR_EXPIRED", "after_event_id 이후 기록을 이어서 조회할 수 없습니다."));
        }
        Specification<VmEvent> filter = Specification.allOf(
                matching(query.vmId() == null ? Set.of() : Set.of(query.vmId()),
                        query.type() == null ? Set.of() : Set.of(query.type())),
                query.severity() == null ? null : (root, q, cb) -> cb.equal(root.get("severity"), query.severity()),
                query.from() == null ? null : (root, q, cb) -> cb.greaterThanOrEqualTo(root.get("recordedAt"), query.from()),
                query.to() == null ? null : (root, q, cb) -> cb.lessThan(root.get("recordedAt"), query.to()),
                after(afterId));
        Sort sort = afterId == null ? Sort.by(Sort.Direction.DESC, "id") : Sort.by(Sort.Direction.ASC, "id");
        return repository.findAll(filter, PageRequest.of(0, query.limit(), sort)).getContent();
    }

    // Events stored after the given one, for a WS-02 reconnect. Empty when the cursor is unknown
    // or the gap is larger than max, in which case the client has to resync over REST.
    @Transactional(readOnly = true)
    public Optional<List<VmEvent>> replayAfter(String afterEventId, Set<String> vmIds, Set<EventType> types, int max) {
        Optional<Long> afterId = cursor(afterEventId);
        if (afterId.isEmpty()) {
            return Optional.empty();
        }
        List<VmEvent> events = repository.findAll(
                Specification.allOf(matching(vmIds, types), after(afterId.get())),
                PageRequest.of(0, max + 1, Sort.by(Sort.Direction.ASC, "id"))).getContent();
        return events.size() > max ? Optional.empty() : Optional.of(events);
    }

    private Optional<Long> cursor(String eventId) {
        return repository.findByEventId(eventId).map(VmEvent::getId);
    }

    private static Specification<VmEvent> matching(Set<String> vmIds, Set<EventType> types) {
        return Specification.allOf(
                vmIds.isEmpty() ? null : (root, q, cb) -> root.get("vmId").in(vmIds),
                types.isEmpty() ? null : (root, q, cb) -> root.get("type").in(types));
    }

    private static Specification<VmEvent> after(Long id) {
        return id == null ? null : (root, q, cb) -> cb.greaterThan(root.get("id"), id);
    }

    public record EventQuery(
            String vmId,
            EventType type,
            EventSeverity severity,
            Instant from,
            Instant to,
            String afterEventId,
            int limit) {
    }
}

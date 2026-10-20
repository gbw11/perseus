package com.xenova.backend.event.api;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.event.application.EventQueryService;
import com.xenova.backend.event.application.EventQueryService.EventQuery;
import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import java.time.Instant;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/events")
public class EventController {

    private final EventQueryService service;

    public EventController(EventQueryService service) {
        this.service = service;
    }

    // REST-18: the event timeline, and the gap to refill after a WS-02 disconnect (after_event_id).
    @GetMapping
    public EventListResponse list(
            @RequestParam(name = "vm_id", required = false) String vmId,
            @RequestParam(required = false) String type,
            @RequestParam(required = false) String severity,
            @RequestParam(required = false) Instant from,
            @RequestParam(required = false) Instant to,
            @RequestParam(name = "after_event_id", required = false) String afterEventId,
            @RequestParam(defaultValue = "" + EventQueryService.DEFAULT_LIMIT) int limit) {
        EventType parsedType = type == null ? null : EventType.fromValue(type)
                .orElseThrow(() -> new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "지원하지 않는 type입니다: " + type));
        EventSeverity parsedSeverity = severity == null ? null : EventSeverity.fromValue(severity)
                .orElseThrow(() -> new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "지원하지 않는 severity입니다: " + severity));

        return new EventListResponse(service.find(
                        new EventQuery(vmId, parsedType, parsedSeverity, from, to, afterEventId, limit))
                .stream()
                .map(EventResponse::of)
                .toList());
    }

    public record EventListResponse(List<EventResponse> items) {
    }
}

package com.xenova.backend.display.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.NullNode;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.display.application.Display;
import com.xenova.backend.display.application.DisplayRegistry;
import java.time.Clock;
import java.time.Instant;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

// REST-23 / REST-24 (S15P31E110-129). Live updates of the same list go out on WS /ws/v1/displays; the route
// itself goes to the dom0 display service on WS /ws/v1/display-service.
@RestController
public class DisplayController {

    private final DisplayRegistry registry;
    private final Clock clock;

    public DisplayController(DisplayRegistry registry, Clock clock) {
        this.registry = registry;
        this.clock = clock;
    }

    @GetMapping("/api/v1/displays")
    public DisplayListResponse list() {
        return new DisplayListResponse(registry.list(), clock.instant(), false);
    }

    // 202: the display service still has to move the kiosk; status stays SWITCHING until it answers route.applied.
    @PutMapping("/api/v1/displays/{displayId}/route")
    public ResponseEntity<Display> changeRoute(
            @PathVariable String displayId, @RequestBody(required = false) JsonNode body,
            Authentication authentication) {
        JsonNode request = body == null ? NullNode.getInstance() : body;
        Display display = registry.changeRoute(
                displayId, text(request, "vm_id"), text(request, "view"), revision(request), username(authentication));
        return ResponseEntity.status(HttpStatus.ACCEPTED).body(display);
    }

    // Read by hand so a wrong type is a 422 VALIDATION_ERROR rather than a body-parsing failure.
    private static String text(JsonNode body, String field) {
        JsonNode value = body.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual()) {
            throw invalid(field + " 는 문자열이어야 합니다.");
        }
        return value.asText();
    }

    private static Long revision(JsonNode body) {
        JsonNode value = body.get("expected_revision");
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isIntegralNumber()) {
            throw invalid("expected_revision 은 정수여야 합니다.");
        }
        return value.asLong();
    }

    private static ApiException invalid(String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", message);
    }

    // The JWT carries the login name as the "username" claim; the principal itself is the user id.
    private static String username(Authentication authentication) {
        if (authentication == null) {
            return null;
        }
        if (authentication.getDetails() instanceof Jwt jwt && jwt.getClaimAsString("username") != null) {
            return jwt.getClaimAsString("username");
        }
        return authentication.getName();
    }

    public record DisplayListResponse(List<Display> items, Instant observedAt, boolean stale) {
    }
}

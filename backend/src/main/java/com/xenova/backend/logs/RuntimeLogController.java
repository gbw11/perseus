package com.xenova.backend.logs;

import com.fasterxml.jackson.databind.JsonNode;
import com.xenova.backend.common.error.ApiException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Browser JWT -> existing server-only dom0 token. No input/execute endpoint. */
@RestController
@RequestMapping("/api/v1/logs")
public class RuntimeLogController {
    private final RuntimeLogPort runtimeLogs;

    public RuntimeLogController(RuntimeLogPort runtimeLogs) {
        this.runtimeLogs = runtimeLogs;
    }

    @GetMapping
    public JsonNode list(@RequestParam(required = false) Long after,
            @RequestParam(name = "source_id", required = false) String sourceId,
            @RequestParam(name = "vm_id", required = false) UUID vmId,
            @RequestParam(required = false) String component,
            @RequestParam(required = false) String level,
            @RequestParam(defaultValue = "200") int limit) {
        if (limit < 1 || limit > 500 || (after != null && after < 0)
                || (sourceId != null && sourceId.length() > 128)
                || (component != null && component.length() > 128)
                || (level != null && !java.util.Set.of("DEBUG", "INFO", "WARN", "ERROR", "FATAL").contains(level))) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "로그 조회 조건을 확인해 주세요.");
        }
        Map<String, Object> filters = new LinkedHashMap<>();
        filters.put("limit", limit);
        if (after != null) filters.put("after", after);
        if (sourceId != null) filters.put("source_id", sourceId);
        if (vmId != null) filters.put("vm_id", vmId);
        if (component != null) filters.put("component", component);
        if (level != null) filters.put("level", level);
        return read(filters, false);
    }

    @GetMapping("/sources")
    public JsonNode sources() {
        return read(Map.of(), true);
    }

    private JsonNode read(Map<String, Object> filters, boolean sources) {
        try {
            return runtimeLogs.read(filters, sources);
        } catch (ApiException error) {
            if (error.getStatus().is5xxServerError()) {
                throw new ApiException(error.getStatus(), "LOG_SOURCE_UNAVAILABLE",
                        "실행 로그 수집기에 연결할 수 없습니다. 이전 기록의 연결 상태를 확인해 주세요.");
            }
            throw error;
        }
    }
}

package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.xenova.backend.agent.domain.MetricSampleData.CpuWindow;
import com.xenova.backend.common.UnsignedDecimal;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/** Validates the bounded Telemetry 2.1 CPU page before preserving its window in metric history. */
final class ZephyrCpuPage {
    private static final int ROWS_PER_PAGE = 4;
    private static final int MAX_ROWS = 32;
    private static final Set<String> UNAVAILABLE = Set.of(
            "unsupported", "not_registered", "not_requested", "no_sample",
            "collection_failed", "scan_limit", "saturated", "counter_discontinuity");

    private ZephyrCpuPage() {
    }

    static CpuWindow parse(JsonNode root, JsonNode window, JsonNode samples) {
        String publisher = uuid(root.path("publisher_instance_id"));
        uuid(root.path("boot_id"));
        String seq = decimal(window, "sample_seq");
        String start = decimal(window, "start_uptime_ms");
        String end = decimal(window, "end_uptime_ms");
        int part = integer(window, "part_index");
        int count = integer(window, "part_count");
        int returned = integer(window, "rows_returned");
        Integer seen = window.path("rows_seen").isNull() ? null : integer(window, "rows_seen");
        String status = window.path("threads_status").asText("");
        boolean validRows = switch (status) {
            case "ok" -> seen != null && seen == returned;
            case "truncated" -> seen != null && seen > returned;
            default -> UNAVAILABLE.contains(status) && seen == null && returned == 0;
        };
        if (!"guest_accounted_up".equals(window.path("scope").asText())
                || UnsignedDecimal.compare(start, end) > 0 || returned > MAX_ROWS || !validRows
                || count != Math.max(1, (returned + ROWS_PER_PAGE - 1) / ROWS_PER_PAGE) || part >= count) {
            throw new IllegalArgumentException("invalid CPU window");
        }
        Set<String> ids = new HashSet<>();
        int systemRows = 0;
        for (JsonNode sample : samples) {
            String name = sample.path("name").asText("");
            if ("cpu.system.percent".equals(name)) {
                systemRows++;
            } else if ("cpu.thread.percent".equals(name)) {
                JsonNode labels = sample.path("labels");
                String id = decimal(labels, "thread_id");
                JsonNode threadName = labels.path("thread_name");
                String truncated = labels.path("name_truncated").asText("");
                if ("0".equals(id) || !ids.add(id) || !threadName.isTextual()
                        || threadName.textValue().getBytes(StandardCharsets.UTF_8).length > 31
                        || (!"true".equals(truncated) && !"false".equals(truncated))) {
                    throw new IllegalArgumentException("invalid CPU thread identity");
                }
            } else {
                throw new IllegalArgumentException("invalid CPU metric name");
            }
            JsonNode value = sample.path("value");
            String state = sample.path("status").asText("");
            boolean validValue = "ok".equals(state)
                    ? value.isNumber() && value.asDouble() >= 0 && value.asDouble() <= 100
                    : UNAVAILABLE.contains(state) && value.isNull();
            if (!"percent".equals(sample.path("unit").asText()) || !validValue) {
                throw new IllegalArgumentException("invalid CPU metric value");
            }
        }
        if (systemRows != 1 || ids.size() != Math.min(ROWS_PER_PAGE, returned - part * ROWS_PER_PAGE)) {
            throw new IllegalArgumentException("invalid CPU page row count");
        }
        return new CpuWindow(publisher, seq, start, end, part, count, status, seen, returned);
    }

    private static String decimal(JsonNode node, String field) {
        return UnsignedDecimal.read(node.path(field), field);
    }

    private static int integer(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() < 0) {
            throw new IllegalArgumentException("invalid " + field);
        }
        return value.intValue();
    }

    private static String uuid(JsonNode node) {
        String value = node.asText("");
        if (!UUID.fromString(value).toString().equals(value)) {
            throw new IllegalArgumentException("invalid CPU sample identity");
        }
        return value;
    }
}

package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.xenova.backend.agent.domain.MetricSampleData.BufferObservation;
import com.xenova.backend.agent.domain.MetricSampleData.CollectionObservation;
import com.xenova.backend.agent.domain.MetricSampleData.ExecutionContext;
import com.xenova.backend.agent.domain.MetricSampleData.TelemetryObservation;
import com.xenova.backend.common.UnsignedDecimal;
import java.math.BigInteger;
import java.time.DateTimeException;
import java.time.Instant;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Keeps cached memory observations separate from collector and transport times; it grants no execution authority. */
final class TelemetryMemoryObservation {
    private static final Set<String> AUTO_REGIONS = Set.of(
            "drive_maps", "drive_paths", "drive_search_nodes", "drive_odometry");
    private static final Map<String, String> MANUAL_PHASES = Map.of(
            "manual_input", "stored", "manual_pending", "pending",
            "manual_running", "running", "manual_result", "retained");
    private static final Set<String> BUFFER_FIELDS = Set.of("phase", "content_bytes", "sequence", "source_uptime_ms",
            "owner_lifetime", "context_revision", "connection_revision", "input_status", "source_rc",
            "performed", "revoked", "last_read_sequence");
    private static final Set<String> INPUT_STATUSES = Set.of(
            "missing", "unclassified", "accepted", "invalid", "expired", "context_changed");
    private static final Set<String> SAMPLE_STATUSES = Set.of("ok", "unsupported", "not_registered", "not_requested",
            "no_sample", "collection_failed", "scan_limit", "saturated", "counter_discontinuity");
    private static final Set<String> GAUGES = Set.of("memory.app.reserved_bytes", "memory.app.used_bytes",
            "memory.app.free_bytes", "memory.app.capacity_bytes", "memory.app.high_water_bytes",
            "memory.app.min_free_bytes");
    private static final Set<String> COUNTERS = Set.of("memory.app.capacity_failure_total", "memory.app.contention_total");

    private TelemetryMemoryObservation() {
    }

    static TelemetryObservation parse(JsonNode root) {
        JsonNode payload = root.path("payload");
        String family = payload.path("family").asText("");
        if (!Set.of("app_memory", "memory", "memory_regions").contains(family)) {
            return null;
        }
        boolean sourceSeq = payload.has("source_sample_seq");
        boolean sourceUptime = payload.has("observed_uptime_ms");
        if (!sourceSeq && !sourceUptime && !"app_memory".equals(family)) {
            // Formal older memory envelopes still identify the direct heap collection.
            // Minimal legacy numeric envelopes have no owner/collector metadata to assert.
            boolean formalMemory = "memory".equals(family) && (root.has("publisher_instance_id")
                    || root.has("context") || payload.has("collection"));
            if (!formalMemory) return null;
        }
        require(sourceSeq == sourceUptime && (!"app_memory".equals(family) || sourceSeq), "incomplete memory source time");
        String seq = sourceSeq ? UnsignedDecimal.read(payload.path("source_sample_seq"), "source_sample_seq") : null;
        String observed = sourceUptime ? UnsignedDecimal.read(payload.path("observed_uptime_ms"), "observed_uptime_ms") : null;
        uuid(root.path("vm_id"), "vm_id");
        String publisher = uuid(root.path("publisher_instance_id"), "publisher_instance_id");
        String messageSeq = UnsignedDecimal.read(root.path("seq"), "seq");
        uuid(root.path("boot_id"), "boot_id");
        JsonNode clock = root.path("clock");
        String sent = UnsignedDecimal.read(clock.path("sent_uptime_ms"), "sent_uptime_ms");
        JsonNode collection = payload.path("collection");
        String start = UnsignedDecimal.read(collection.path("start_uptime_ms"), "collection start");
        String end = UnsignedDecimal.read(collection.path("end_uptime_ms"), "collection end");
        require(UnsignedDecimal.compare(start, end) <= 0 && UnsignedDecimal.compare(end, sent) <= 0
                && (observed == null || UnsignedDecimal.compare(observed, end) <= 0), "inverted memory observation time");
        String elapsed = nullableDecimal(collection.path("elapsed_us"), "elapsed_us");
        String collectionStatus = collection.path("status").asText("");
        require(Set.of("ok", "collection_failed").contains(collectionStatus), "invalid collection status");
        Integer collectionRc = nullableRc(collection.path("source_rc"));
        require(collectionRc != null && ("ok".equals(collectionStatus) ? collectionRc == 0 : collectionRc < 0),
                "collection status differs from source_rc");
        CollectionObservation collected = new CollectionObservation(
                uuid(collection.path("sample_id"), "collection sample_id"), start, end, elapsed,
                collectionStatus, collectionRc);
        ExecutionContext context = context(root.path("context"));
        String quality = clock.path("utc_quality").asText("");
        require(Set.of("unavailable", "synchronized").contains(quality), "invalid UTC quality");
        String sentAt = nullableText(clock.path("sent_at"), "sent_at", 40);
        if (sentAt != null) {
            try {
                Instant.parse(sentAt);
            } catch (DateTimeException exception) {
                throw new IllegalArgumentException("invalid sent_at");
            }
        }
        require("synchronized".equals(quality) == (sentAt != null), "inconsistent UTC clock");
        String utcSource = nullableText(clock.path("utc_source"), "utc_source", 64);
        JsonNode uncertainty = clock.path("uncertainty_ms");
        require(uncertainty.isNull() || uncertainty.isIntegralNumber() && uncertainty.canConvertToInt()
                && uncertainty.intValue() >= 0 && uncertainty.intValue() <= 3_600_000, "invalid UTC uncertainty");
        BufferObservation buffer = "app_memory".equals(family) ? appBuffer(payload, end) : null;
        require("app_memory".equals(family) || !payload.has("buffer"), "buffer outside app_memory");
        return new TelemetryObservation(family, publisher, seq, observed, sent, sentAt, quality,
                utcSource, uncertainty.isNull() ? null : uncertainty.intValue(), collected, context, buffer, messageSeq);
    }

    private static BufferObservation appBuffer(JsonNode payload, String collectionEnd) {
        JsonNode samples = payload.path("samples");
        require(samples.isArray() && samples.size() == GAUGES.size() + COUNTERS.size(), "incomplete app memory region");
        String region = null;
        Set<String> names = new HashSet<>();
        Map<String, JsonNode> byName = new LinkedHashMap<>();
        for (JsonNode sample : samples) {
            String name = sample.path("name").asText("");
            boolean gauge = GAUGES.contains(name);
            require((gauge || COUNTERS.contains(name)) && names.add(name), "invalid app memory metric");
            require("u64_decimal".equals(sample.path("encoding").asText())
                    && (gauge ? "gauge" : "raw_counter").equals(sample.path("kind").asText())
                    && (gauge ? "bytes" : "count").equals(sample.path("unit").asText()), "invalid app memory encoding");
            String currentRegion = sample.path("labels").path("region").asText("");
            require((AUTO_REGIONS.contains(currentRegion) || MANUAL_PHASES.containsKey(currentRegion))
                    && (region == null || region.equals(currentRegion)),
                    "mixed or invalid app memory region");
            region = currentRegion;
            nullableRc(sample.path("source_rc"));
            byName.put(name, sample);
        }
        if (AUTO_REGIONS.contains(region)) {
            // Preserve the previous AUTO diagnostics path, including malformed-number parse_error evidence.
            require(!payload.has("buffer"), "AUTO region has MANUAL buffer");
            return null;
        }
        require(payload.has("buffer"), "missing MANUAL buffer");
        JsonNode first = samples.get(0);
        String status = first.path("status").asText("");
        require(SAMPLE_STATUSES.contains(status), "invalid MANUAL collection status");
        boolean observed = "ok".equals(status);
        for (JsonNode sample : samples) {
            require(first.path("status").equals(sample.path("status"))
                    && first.path("reason").equals(sample.path("reason"))
                    && first.path("source_rc").equals(sample.path("source_rc")), "mixed MANUAL observation status");
            JsonNode reason = sample.path("reason");
            require(reason.isNull() || reason.isTextual() && reason.textValue().length() <= 128,
                    "invalid MANUAL observation reason");
            require(observed ? reason.isNull() && sample.path("source_rc").isIntegralNumber()
                    && sample.path("source_rc").intValue() == 0 : sample.path("value").isNull(),
                    "MANUAL observation status differs from value");
        }
        JsonNode buffer = payload.path("buffer");
        if (!observed) {
            require(buffer.isNull(), "unobserved MANUAL buffer must be null");
            return null;
        }
        Map<String, BigInteger> amounts = new LinkedHashMap<>();
        for (Map.Entry<String, JsonNode> entry : byName.entrySet()) {
            amounts.put(entry.getKey(), new BigInteger(UnsignedDecimal.read(entry.getValue().path("value"), entry.getKey())));
        }
        BigInteger used = amounts.get("memory.app.used_bytes");
        BigInteger capacity = amounts.get("memory.app.capacity_bytes");
        BigInteger peak = amounts.get("memory.app.high_water_bytes");
        require(used.compareTo(peak) <= 0 && peak.compareTo(capacity) <= 0
                && capacity.compareTo(amounts.get("memory.app.reserved_bytes")) <= 0
                && used.add(amounts.get("memory.app.free_bytes")).equals(capacity)
                && peak.add(amounts.get("memory.app.min_free_bytes")).equals(capacity), "inconsistent MANUAL slot accounting");
        if ("manual_input".equals(region)) {
            require(amounts.get("memory.app.contention_total").compareTo(BigInteger.valueOf(0xffff_ffffL)) <= 0,
                    "manual_input contention exceeds original 32-bit source");
        }
        return buffer(buffer, region, used, collectionEnd);
    }

    private static BufferObservation buffer(JsonNode node, String region, BigInteger used, String collectionEnd) {
        Set<String> fields = new HashSet<>();
        node.fieldNames().forEachRemaining(fields::add);
        require(node.isObject() && fields.equals(BUFFER_FIELDS), "invalid MANUAL buffer fields");
        String phase = node.path("phase").asText("");
        require("empty".equals(phase) || MANUAL_PHASES.get(region).equals(phase), "MANUAL phase differs from owner");
        String content = UnsignedDecimal.read(node.path("content_bytes"), "buffer content_bytes");
        require(new BigInteger(content).compareTo(used) <= 0, "MANUAL content exceeds owned slot");
        String sequence = nullableDecimal(node.path("sequence"), "buffer sequence");
        String sourceUptime = nullableDecimal(node.path("source_uptime_ms"), "buffer source_uptime_ms");
        if ("empty".equals(phase)) {
            require(sequence == null && sourceUptime == null && "0".equals(content) && used.signum() == 0,
                    "empty MANUAL buffer has retained content");
        } else {
            require(sequence != null, "stored MANUAL buffer has no sequence");
            require(sourceUptime == null || UnsignedDecimal.compare(sourceUptime, collectionEnd) <= 0,
                    "MANUAL original input time is in the future");
        }
        String lifetime = positiveNullableDecimal(node.path("owner_lifetime"), "buffer owner_lifetime");
        String lastRead = positiveNullableDecimal(node.path("last_read_sequence"), "buffer last_read_sequence");
        String contextRevision = UnsignedDecimal.read(node.path("context_revision"), "buffer context_revision");
        String connectionRevision = UnsignedDecimal.read(node.path("connection_revision"), "buffer connection_revision");
        String inputStatus = node.path("input_status").asText("");
        require(INPUT_STATUSES.contains(inputStatus), "invalid MANUAL input status");
        Integer rc = nullableRc(node.path("source_rc"));
        require(rc != null && node.path("revoked").isBoolean(), "invalid MANUAL buffer source/withdrawal");
        JsonNode performed = node.path("performed");
        require("manual_result".equals(region) ? performed.isBoolean() : performed.isNull() && lastRead == null,
                "only MANUAL result has performed/read evidence");
        return new BufferObservation(phase, content, sequence, sourceUptime, lifetime,
                contextRevision, connectionRevision, inputStatus, rc,
                performed.isBoolean() ? performed.booleanValue() : null, node.path("revoked").booleanValue(), lastRead);
    }

    private static String positiveNullableDecimal(JsonNode node, String field) {
        String value = nullableDecimal(node, field);
        require(value == null || !"0".equals(value), field + " must be positive or null");
        return value;
    }

    private static ExecutionContext context(JsonNode node) {
        String binding = node.path("binding").asText("");
        require(Set.of("unbound", "host_provisioned").contains(binding), "invalid memory binding");
        String host = nullableUuid(node.path("host_boot_id"), "host_boot_id");
        String execution = nullableUuid(node.path("execution_id"), "execution_id");
        String receipt = nullableUuid(node.path("receipt_id"), "receipt_id");
        require("unbound".equals(binding) ? host == null && execution == null && receipt == null
                : host != null && execution != null && receipt != null, "inconsistent memory binding");
        return new ExecutionContext(binding, host, execution, receipt);
    }

    private static String nullableUuid(JsonNode node, String field) {
        return node.isNull() ? null : uuid(node, field);
    }

    private static String uuid(JsonNode node, String field) {
        String value = node.isTextual() ? node.textValue() : "";
        require(value.length() == 36 && UUID.fromString(value).toString().equals(value), "invalid " + field);
        return value;
    }

    private static String nullableDecimal(JsonNode node, String field) {
        return node.isNull() ? null : UnsignedDecimal.read(node, field);
    }

    private static String nullableText(JsonNode node, String field, int maxLength) {
        if (node.isNull()) {
            return null;
        }
        require(node.isTextual() && !node.textValue().isBlank() && node.textValue().length() <= maxLength,
                "invalid " + field);
        return node.textValue();
    }

    private static Integer nullableRc(JsonNode node) {
        require(node.isNull() || node.isIntegralNumber() && node.canConvertToInt() && node.intValue() <= 0,
                "invalid source_rc");
        return node.isNull() ? null : node.intValue();
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }
}

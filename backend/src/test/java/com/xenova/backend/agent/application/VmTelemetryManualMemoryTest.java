package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.xenova.backend.agent.domain.AgentHeartbeatRepository;
import com.xenova.backend.agent.domain.AgentInventoryRepository;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleData;
import com.xenova.backend.agent.domain.MetricSampleRepository;
import com.xenova.backend.agent.domain.MetricValuesConverter;
import com.xenova.backend.agent.domain.ProcessSnapshotRepository;
import com.xenova.backend.agent.domain.TelemetryHeartbeatRepository;
import com.xenova.backend.agent.infrastructure.AgentProperties;
import com.xenova.backend.metrics.api.MetricsController;
import com.xenova.backend.metrics.application.MetricsQueryService;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

/** Product receiver/service/JSON converter/query/HTTP projection; only the repository is a memory boundary. */
class VmTelemetryManualMemoryTest {
    private static final Instant NOW = Instant.parse("2026-11-04T07:00:00Z");
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
    private final MetricSampleRepository repository = mock(MetricSampleRepository.class);
    private final Map<String, MetricSample> rows = new LinkedHashMap<>();
    private final List<Object> events = new ArrayList<>();
    private final AgentDataService data = new AgentDataService(mock(AgentInventoryRepository.class),
            mock(AgentHeartbeatRepository.class), mock(TelemetryHeartbeatRepository.class), repository,
            mock(ProcessSnapshotRepository.class), new AgentProperties(Duration.ofSeconds(15), Duration.ofMinutes(1)),
            events::add);
    private final VmTelemetryMessageHandler handler = new VmTelemetryMessageHandler(mapper, data, mock(VmRecordRepository.class));
    private final MockMvc http = MockMvcBuilders.standaloneSetup(
                    new MetricsController(new MetricsQueryService(data, Clock.fixed(NOW, ZoneOffset.UTC))))
            .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();

    @BeforeEach
    void jsonRepositoryBoundary() {
        when(repository.existsById(any())).thenAnswer(call -> rows.containsKey(call.getArgument(0)));
        when(repository.save(any())).thenAnswer(call -> {
            MetricSample sample = call.getArgument(0);
            MetricValuesConverter converter = new MetricValuesConverter();
            MetricSampleData values = new MetricSampleData(sample.getValues(), sample.getNetwork(), sample.getCpuWindow(),
                    sample.getTelemetry());
            MetricSample restored = new MetricSample(sample.getSampleId(), sample.getVmId(), sample.getBootId(),
                    sample.getSource(), sample.getObservedAt(), sample.getReceivedAt(), sample.getWindowMs(),
                    converter.convertToEntityAttribute(converter.convertToDatabaseColumn(values)));
            rows.put(restored.getSampleId(), restored);
            return restored;
        });
        when(repository.findByVmIdAndObservedAtGreaterThanEqualAndObservedAtLessThanOrderByObservedAtAsc(any(), any(), any()))
                .thenAnswer(call -> rows.values().stream()
                        .filter(sample -> sample.getVmId().equals(call.getArgument(0)))
                        .filter(sample -> !sample.getObservedAt().isBefore(call.getArgument(1)))
                        .filter(sample -> sample.getObservedAt().isBefore(call.getArgument(2)))
                        .sorted(Comparator.comparing(MetricSample::getObservedAt)).toList());
    }

    @ParameterizedTest(name = "preserves {0}")
    @MethodSource("validFixtures")
    void providerVectorsAndNativeCapturesPreserveExactBufferThroughStorageAndHttp(String file) throws Exception {
        ObjectNode message = fixture(file);
        assertThat(ingest(message)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        assertThat(rows).hasSize(1);
        assertThat(events).hasSize(1);
        JsonNode item = history(message.path("vm_id").asText()).path("items").get(0);
        assertThat(item.path("observed_at").isTextual()).isTrue();
        assertThat(Instant.parse(item.path("observed_at").asText())).isNotNull();
        assertThat(item.path("received_at").asText()).isEqualTo(NOW.toString());
        assertThat(item.path("telemetry").path("buffer")).isEqualTo(message.path("payload").path("buffer"));
        assertThat(item.path("telemetry").path("message_seq")).isEqualTo(message.path("seq"));
        assertThat(item.path("telemetry").path("source_sample_seq"))
                .isEqualTo(message.path("payload").path("source_sample_seq"));
        assertThat(item.path("telemetry").path("observed_uptime_ms"))
                .isEqualTo(message.path("payload").path("observed_uptime_ms"));
        for (JsonNode original : message.path("payload").path("samples")) {
            JsonNode actual = metric(item, original.path("name").asText());
            assertThat(actual.path("raw_value").isMissingNode() ? mapper.getNodeFactory().nullNode() : actual.path("raw_value"))
                    .isEqualTo(original.path("value"));
            assertThat(actual.path("status")).isEqualTo(original.path("status"));
            assertThat(actual.path("source_rc")).isEqualTo(original.path("source_rc"));
        }
    }

    @ParameterizedTest(name = "rejects {0}")
    @MethodSource("invalidFixtures")
    void rejectsProviderCounterexamplesBeforeSavingAndNormalUseStillWorks(String file) throws Exception {
        assertThat(ingest(fixture(file))).isEqualTo(AgentMessageHandler.Result.REJECTED);
        verify(repository, never()).save(any());
        assertThat(events).isEmpty();
        assertThat(ingest(fixture("fixtures/manual-memory-input-seq-zero.json")))
                .isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        assertThat(rows).hasSize(1);
    }

    @Test
    void invalidExpiredInputAndRetainedReadResultsAreNotMisrepresentedAsUnobservedOrReleased() throws Exception {
        ObjectNode expired = fixture("fixtures/manual-memory-input-expired.json");
        ingest(expired);
        MetricSample input = rows.values().iterator().next();
        assertThat(input.getTelemetry().buffer().inputStatus()).isEqualTo("expired");
        assertThat(input.getValues()).allSatisfy(value -> assertThat(value.status()).isEqualTo("ok"));
        assertThat(input.getValues().stream().filter(value -> value.name().equals("memory.app.used_bytes"))
                .findFirst().orElseThrow().rawValue()).isNotEqualTo("0");

        rows.clear();
        ObjectNode retained = fixture("native/manual_result.json");
        ingest(retained);
        var result = rows.values().iterator().next().getTelemetry().buffer();
        assertThat(result.phase()).isEqualTo("retained");
        assertThat(result.contentBytes()).isEqualTo("2592");
        assertThat(result.performed()).isTrue();
        assertThat(result.sequence()).isEqualTo("200");
        assertThat(result.lastReadSequence()).isEqualTo("199");
    }

    @Test
    void knownZeroOriginalSequenceAndTimeRemainDifferentFromUnknownAndFalseFromNull() throws Exception {
        ObjectNode zero = fixture("fixtures/manual-memory-input-seq-zero.json");
        ingest(zero);
        var input = rows.values().iterator().next().getTelemetry().buffer();
        assertThat(input.sequence()).isEqualTo("0");
        assertThat(input.sourceUptimeMs()).isEqualTo("0");
        assertThat(input.performed()).isNull();
        assertThat(input.revoked()).isFalse();
        rows.clear();
        ingest(fixture("fixtures/manual-memory-result-empty.json"));
        var empty = rows.values().iterator().next().getTelemetry().buffer();
        assertThat(empty.sequence()).isNull();
        assertThat(empty.sourceUptimeMs()).isNull();
        assertThat(empty.performed()).isFalse();
    }

    @ParameterizedTest
    @ValueSource(ints = {-95, -134, -16, -110})
    void keepsPlatformOriginalSourceCodesWithoutLinuxErrnoNormalization(int rc) throws Exception {
        ObjectNode message = fixture("fixtures/manual-memory-input-invalid.json");
        ((ObjectNode) message.path("payload").path("buffer")).put("source_rc", rc);
        ingest(message);
        assertThat(rows.values().iterator().next().getTelemetry().buffer().sourceRc()).isEqualTo(rc);
        assertThat(rows.values().iterator().next().getValues()).allSatisfy(value -> assertThat(value.sourceRc()).isZero());
    }

    @ParameterizedTest
    @ValueSource(strings = {"0", "18446744073709551615"})
    void envelopeSequenceHasItsOwnExactUnsignedIdentity(String sequence) throws Exception {
        ObjectNode message = fixture("fixtures/manual-memory-input-seq-zero.json");
        message.put("seq", sequence);
        ingest(message);
        var observation = rows.values().iterator().next().getTelemetry();
        assertThat(observation.messageSeq()).isEqualTo(sequence);
        assertThat(observation.buffer().sequence()).isEqualTo("0");
    }

    @Test
    void newOptionalBufferAndEnvelopeSequenceDoNotPreventReadingThePriorTelemetryObject() throws Exception {
        ingest(fixture("native/manual_result.json"));
        MetricSample sample = rows.values().iterator().next();
        MetricValuesConverter converter = new MetricValuesConverter();
        ObjectNode old = (ObjectNode) new ObjectMapper().readTree(converter.convertToDatabaseColumn(
                new MetricSampleData(sample.getValues(), sample.getNetwork(), sample.getCpuWindow(), sample.getTelemetry())));
        ((ObjectNode) old.path("telemetry")).remove(List.of("buffer", "messageSeq"));
        MetricSampleData restored = converter.convertToEntityAttribute(old.toString());
        assertThat(restored.telemetry().buffer()).isNull();
        assertThat(restored.telemetry().messageSeq()).isNull();
        assertThat(restored.telemetry().sourceSampleSeq()).isEqualTo(sample.getTelemetry().sourceSampleSeq());
    }

    @Test
    void nativeObservationErrnoIsPreservedWhenTheOwnerCannotBeObserved() throws Exception {
        ObjectNode message = fixture("fixtures/manual-memory-busy.json");
        for (JsonNode value : message.path("payload").path("samples")) ((ObjectNode) value).put("source_rc", -134);
        ingest(message);
        MetricSample sample = rows.values().iterator().next();
        assertThat(sample.getTelemetry().buffer()).isNull();
        assertThat(sample.getValues()).allSatisfy(value -> {
            assertThat(value.sourceRc()).isEqualTo(-134);
            assertThat(value.status()).isEqualTo("collection_failed");
            assertThat(value.rawValue()).isNull();
            assertThat(value.unavailableReason()).isEqualTo("owner_busy");
        });
    }

    @ParameterizedTest
    @ValueSource(strings = {"ram-committed.json", "ram-unsupported.json", "ram-source-expired.json", "ram-free-over-total.json"})
    void ramWholePayloadRetainsExactSourceEvidenceEvenWhenItsDerivedSummaryMustBeUnknown(String name) throws Exception {
        ObjectNode message = fixture("ram/" + name);
        assertThat(ingest(message)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        JsonNode item = history(message.path("vm_id").asText()).path("items").get(0);
        assertThat(item.path("telemetry").path("source_sample_seq")).isEqualTo(message.path("payload").path("source_sample_seq"));
        assertThat(item.path("telemetry").path("observed_uptime_ms")).isEqualTo(message.path("payload").path("observed_uptime_ms"));
        assertThat(item.path("telemetry").path("message_seq")).isEqualTo(message.path("seq"));
        for (String field : List.of("ram_region_bytes", "mapped_bytes", "static_data_bytes", "free_bytes")) {
            String metricName = (field.equals("free_bytes") ? "memory.page_allocator." : "memory.firmware.") + field;
            JsonNode original = metricFromWire(message, metricName);
            JsonNode actual = metric(item, metricName);
            assertThat(actual.path("raw_value").isMissingNode() ? mapper.getNodeFactory().nullNode() : actual.path("raw_value"))
                    .isEqualTo(original.path("value"));
            assertThat(actual.path("status")).isEqualTo(original.path("status"));
            assertThat(actual.path("source_rc")).isEqualTo(original.path("source_rc"));
        }
    }

    @Test
    void aFreshHeapEnvelopeDoesNotRewriteTheCachedRamSourcePair() throws Exception {
        ObjectNode first = fixture("ram/ram-committed.json");
        ObjectNode second = fixture("ram/ram-heap-changed.json");
        second.put("seq", "2");
        second.put("message_id", second.path("message_id").asText().replace("/metrics/1", "/metrics/2"));
        ((ObjectNode) second.path("clock")).put("sent_uptime_ms", "10010");
        ObjectNode collection = (ObjectNode) second.path("payload").path("collection");
        collection.put("sample_id", "f3000000-0000-4000-8000-000000000102");
        collection.put("start_uptime_ms", "10010").put("end_uptime_ms", "10010");
        assertThat(ingest(first)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        assertThat(ingest(second)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        JsonNode items = history(first.path("vm_id").asText()).path("items");
        assertThat(items).hasSize(2);
        assertThat(items.get(0).path("telemetry").path("message_seq").asText()).isEqualTo("1");
        assertThat(items.get(1).path("telemetry").path("message_seq").asText()).isEqualTo("2");
        for (JsonNode item : items) {
            assertThat(item.path("telemetry").path("source_sample_seq").asText()).isEqualTo("1");
            assertThat(item.path("telemetry").path("observed_uptime_ms").asText()).isEqualTo("10000");
            assertThat(metric(item, "memory.page_allocator.free_bytes").path("raw_value").asText()).isEqualTo("12288");
        }
        assertThat(metric(items.get(0), "memory.kernel_heap.used_bytes").path("raw_value").asText()).isEqualTo("18432");
        assertThat(metric(items.get(1), "memory.kernel_heap.used_bytes").path("raw_value").asText()).isEqualTo("2048");
        assertThat(items.get(1).path("telemetry").path("collection").path("end_uptime_ms").asText()).isEqualTo("10010");
    }

    @Test
    void formalMemoryWithoutCachedSourceStillPreservesDirectHeapCollectionAndBinding() throws Exception {
        ObjectNode message = fixture("ram/ram-source-unidentified.json");
        assertThat(ingest(message)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        JsonNode item = history(message.path("vm_id").asText()).path("items").get(0);
        JsonNode telemetry = item.path("telemetry");
        assertThat(telemetry.path("source_sample_seq").isNull()).isTrue();
        assertThat(telemetry.path("observed_uptime_ms").isNull()).isTrue();
        assertThat(telemetry.path("collection")).isEqualTo(message.path("payload").path("collection"));
        assertThat(telemetry.path("context")).isEqualTo(message.path("context"));
        assertThat(telemetry.path("sent_uptime_ms")).isEqualTo(message.path("clock").path("sent_uptime_ms"));
        assertThat(telemetry.path("publisher_instance_id")).isEqualTo(message.path("publisher_instance_id"));
        assertThat(telemetry.path("message_seq")).isEqualTo(message.path("seq"));
        assertThat(metric(item, "memory.kernel_heap.used_bytes").path("raw_value").asText()).isEqualTo("18432");
        // RAM source stays unidentified: direct heap collection must never fill these cache fields.
        assertThat(rows.values().iterator().next().getTelemetry().observedUptimeMs()).isNull();
    }

    @Test
    void legacyNumericOnlyMemoryEnvelopesRetainValuesWithoutInventedCollectorMetadata() throws Exception {
        ObjectNode message = (ObjectNode) mapper.readTree("""
                {"schema_version":"2.1","type":"telemetry.metrics","message_id":"legacy-memory-1",
                 "vm_id":"f1000000-0000-4000-8000-000000000001","boot_id":"legacy",
                 "seq":"1","clock":{"sent_uptime_ms":"10","sent_at":null},
                 "payload":{"family":"memory","samples":[{"name":"memory.kernel_heap.used_bytes",
                 "unit":"bytes","encoding":"u64_decimal","value":"0","status":"ok","reason":null,"source_rc":0,"labels":{}}]}}
                """);
        assertThat(ingest(message)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        MetricSample stored = rows.values().iterator().next();
        assertThat(stored.getTelemetry()).isNull();
        assertThat(stored.getValues().getFirst().rawValue()).isEqualTo("0");
    }

    @Test
    void partialFormalMemoryMetadataIsRejectedInsteadOfFallingBackToACompleteObservation() throws Exception {
        ObjectNode message = fixture("ram/ram-source-unidentified.json");
        message.remove("context");
        assertThat(ingest(message)).isEqualTo(AgentMessageHandler.Result.REJECTED);
        verify(repository, never()).save(any());
        assertThat(ingest(fixture("ram/ram-source-unidentified.json"))).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        assertThat(rows).hasSize(1);
    }

    @ParameterizedTest(name = "RAM contract {0} with wrong {1}")
    @MethodSource("ramContractMismatches")
    void ramContractMismatchesRemainDiagnosticHistoryAndANewCorrectSourceRecovers(String name, String defect) throws Exception {
        ObjectNode first = fixture("ram/ram-committed.json");
        ObjectNode malformed = fixture("ram/ram-committed.json");
        nextSource(malformed, 2);
        ObjectNode changed = (ObjectNode) metricFromWire(malformed, name);
        switch (defect) {
            case "labels" -> ((ObjectNode) changed.path("labels")).put("scope", "heap");
            case "unit" -> changed.put("unit", "KiB");
            case "kind" -> changed.put("kind", "raw_counter");
            case "encoding" -> changed.put("encoding", "i64_decimal");
            default -> throw new AssertionError(defect);
        }
        ObjectNode recovered = fixture("ram/ram-committed.json");
        nextSource(recovered, 3);
        assertThat(ingest(first)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        assertThat(ingest(malformed)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        JsonNode beforeRecovery = history(first.path("vm_id").asText());
        assertThat(beforeRecovery.path("items")).hasSize(2);
        JsonNode invalid = metric(beforeRecovery.path("items").get(1), name);
        assertThat(invalid.path("value").isNull()).isTrue();
        assertThat(invalid.path("unavailable_reason").asText()).isEqualTo("ram_contract_mismatch");
        assertThat(invalid.path("raw_value")).isEqualTo(changed.path("value"));
        assertThat(invalid.path("status")).isEqualTo(changed.path("status"));
        assertThat(invalid.path("source_rc")).isEqualTo(changed.path("source_rc"));
        assertThat(invalid.path("unit")).isEqualTo(changed.path("unit"));
        assertThat(invalid.path("labels")).isEqualTo(changed.path("labels"));
        assertThat(ingest(recovered)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        JsonNode afterRecovery = history(first.path("vm_id").asText());
        assertThat(afterRecovery.path("items")).hasSize(3);
        JsonNode valid = metric(afterRecovery.path("items").get(2), name);
        assertThat(valid.path("value").isNumber()).isTrue();
        assertThat(valid.path("unavailable_reason").isNull()).isTrue();
        assertThat(valid.path("raw_value")).isEqualTo(metricFromWire(first, name).path("value"));
        assertThat(events).hasSize(3);
        if ("memory.page_allocator.free_bytes".equals(name) && "labels".equals(defect)) {
            ObjectNode capture = mapper.createObjectNode();
            capture.put("scope", "Actual product ingest/JSON converter/MockMvc output; DB repository is a memory boundary. The middle test message explicitly mutates the contract fixture labels to scope=heap; no board or production source is claimed.");
            capture.set("before_recovery", beforeRecovery);
            capture.set("after_recovery", afterRecovery);
            Files.writeString(Path.of("target", "ram-contract-http.json"),
                    mapper.writerWithDefaultPrettyPrinter().writeValueAsString(capture) + "\n");
        }
    }

    @Test
    void sourceFailureReasonAndErrnoHavePriorityOverRamDecoderMismatch() throws Exception {
        ObjectNode message = fixture("ram/ram-unsupported.json");
        ObjectNode failed = (ObjectNode) metricFromWire(message, "memory.page_allocator.free_bytes");
        ((ObjectNode) failed.path("labels")).put("scope", "heap");
        failed.put("reason", "page_source_unavailable").put("source_rc", -134);
        assertThat(ingest(message)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        JsonNode actual = metric(history(message.path("vm_id").asText()).path("items").get(0), "memory.page_allocator.free_bytes");
        assertThat(actual.path("value").isNull()).isTrue();
        assertThat(actual.path("status").asText()).isEqualTo("unsupported");
        assertThat(actual.path("source_rc").intValue()).isEqualTo(-134);
        assertThat(actual.path("unavailable_reason").asText()).isEqualTo("page_source_unavailable");
        assertThat(actual.path("labels").path("scope").asText()).isEqualTo("heap");
    }

    private static Stream<Arguments> ramContractMismatches() {
        return Stream.of("memory.firmware.ram_region_bytes", "memory.firmware.mapped_bytes",
                        "memory.firmware.static_data_bytes", "memory.page_allocator.free_bytes")
                .flatMap(name -> Stream.of("labels", "unit", "kind", "encoding").map(defect -> Arguments.of(name, defect)));
    }

    private static void nextSource(ObjectNode message, int sequence) {
        message.put("message_id", message.path("message_id").asText().replace("/metrics/1", "/metrics/" + sequence));
        message.put("seq", Integer.toString(sequence));
        ObjectNode payload = (ObjectNode) message.path("payload");
        payload.put("source_sample_seq", Integer.toString(sequence));
        String observed = Integer.toString(10_000 + sequence);
        payload.put("observed_uptime_ms", observed);
        ((ObjectNode) payload.path("collection")).put("start_uptime_ms", observed).put("end_uptime_ms", observed)
                .put("sample_id", "d954b599-e129-5526-9068-" + String.format("%012x", (long) sequence));
        ((ObjectNode) message.path("clock")).put("sent_uptime_ms", Integer.toString(10_002 + sequence));
    }

    @Test
    void copiedRawFixturesMatchTheirProvenanceHashes() throws Exception {
        for (JsonNode entry : new ObjectMapper().readTree(bytes("provenance.json")).path("files")) {
            assertThat(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes(entry.path("file").asText()))))
                    .as(entry.path("file").asText()).isEqualTo(entry.path("sha256").asText());
        }
    }

    @Test
    void writesActualHttpProjectionsForTheFrontendInteropCheck() throws Exception {
        ObjectNode capture = mapper.createObjectNode();
        capture.put("scope", "Product handler/service/JSON converter/query/MockMvc output; memory repository replaces MySQL, HTTP auth and production deployment are not exercised.");
        capture.put("received_at", NOW.toString());
        var responses = capture.putArray("responses");
        for (String file : List.of("native/manual_input.json", "native/manual_pending.json", "native/manual_running.json",
                "native/manual_result.json", "ram/ram-committed.json", "ram/ram-source-unidentified.json")) {
            rows.clear();
            ObjectNode message = fixture(file);
            assertThat(ingest(message)).isEqualTo(AgentMessageHandler.Result.ACCEPTED);
            JsonNode body = history(message.path("vm_id").asText());
            assertThat(body.path("items")).hasSize(1);
            assertThat(body.path("items").get(0).path("observed_at").isTextual()).isTrue();
            assertThat(body.path("items").get(0).path("received_at").asText()).isEqualTo(NOW.toString());
            ObjectNode response = responses.addObject();
            response.put("source_fixture", file);
            response.set("body", body);
        }
        Path target = Path.of("target", "manual-memory-http.json");
        Files.createDirectories(target.getParent());
        Files.writeString(target, mapper.writerWithDefaultPrettyPrinter().writeValueAsString(capture) + "\n");
    }

    private static Stream<String> validFixtures() throws Exception {
        return fixturesOf("contract_valid", "native_capture");
    }

    private static Stream<String> invalidFixtures() throws Exception {
        return fixturesOf("contract_invalid");
    }

    private static Stream<String> fixturesOf(String... kinds) throws Exception {
        List<String> expected = List.of(kinds);
        List<String> files = new ArrayList<>();
        for (JsonNode entry : new ObjectMapper().readTree(bytes("provenance.json")).path("files")) {
            if (expected.contains(entry.path("kind").asText())) files.add(entry.path("file").asText());
        }
        return files.stream();
    }

    private AgentMessageHandler.Result ingest(ObjectNode message) throws Exception {
        return handler.handle("xenova/v2/vms/" + message.path("vm_id").asText() + "/telemetry/metrics",
                mapper.writeValueAsBytes(message), NOW);
    }

    private JsonNode history(String vmId) throws Exception {
        String body = http.perform(get("/api/v1/vms/{vmId}/metrics", vmId).param("to", NOW.plusSeconds(1).toString()))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        return mapper.readTree(body);
    }

    private ObjectNode fixture(String file) throws IOException {
        return (ObjectNode) mapper.readTree(bytes(file));
    }

    private static byte[] bytes(String name) throws IOException {
        try (var stream = VmTelemetryManualMemoryTest.class.getResourceAsStream("/telemetry-manual-memory/" + name)) {
            if (stream == null) throw new IOException("missing fixture " + name);
            return stream.readAllBytes();
        }
    }

    private static JsonNode metric(JsonNode sample, String name) {
        for (JsonNode value : sample.path("values")) if (name.equals(value.path("name").asText())) return value;
        throw new AssertionError("missing metric " + name);
    }

    private static JsonNode metricFromWire(JsonNode message, String name) {
        for (JsonNode value : message.path("payload").path("samples")) if (name.equals(value.path("name").asText())) return value;
        throw new AssertionError("missing wire metric " + name);
    }
}

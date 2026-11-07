package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
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
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.agent.domain.MetricValuesConverter;
import com.xenova.backend.agent.domain.ProcessSnapshotRepository;
import com.xenova.backend.agent.domain.TelemetryHeartbeatRepository;
import com.xenova.backend.agent.infrastructure.AgentProperties;
import com.xenova.backend.agent.infrastructure.MqttAgentSubscriber;
import com.xenova.backend.agent.infrastructure.MqttProperties;
import com.xenova.backend.event.application.RecoveryEventBackfillService;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.metrics.api.MetricsController;
import com.xenova.backend.metrics.application.MetricsQueryService;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.io.IOException;
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
import java.util.function.Consumer;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

/** Real receiver, JSON persistence codec, query and REST projection; only repository/DB and MQTT network are replaced. */
class VmTelemetryAppMemoryTest {
    private static final Instant NOW = Instant.parse("2026-11-04T06:00:00Z");
    private static final Clock CLOCK = Clock.fixed(NOW, ZoneOffset.UTC);
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
    private final MetricSampleRepository repository = mock(MetricSampleRepository.class);
    private final Map<String, MetricSample> rows = new LinkedHashMap<>();
    private final List<Object> events = new ArrayList<>();
    private final ApplicationEventPublisher publisher = events::add;
    private final AgentDataService data = new AgentDataService(mock(AgentInventoryRepository.class),
            mock(AgentHeartbeatRepository.class), mock(TelemetryHeartbeatRepository.class), repository,
            mock(ProcessSnapshotRepository.class), new AgentProperties(Duration.ofSeconds(15), Duration.ofMinutes(1)),
            publisher);
    private final VmTelemetryMessageHandler handler = new VmTelemetryMessageHandler(mapper, data, mock(VmRecordRepository.class));
    private final MockMvc mvc = MockMvcBuilders.standaloneSetup(new MetricsController(new MetricsQueryService(data, CLOCK)))
            .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();

    @BeforeEach
    void persistenceBoundary() {
        when(repository.existsById(any())).thenAnswer(call -> rows.containsKey(call.getArgument(0)));
        when(repository.save(any())).thenAnswer(call -> {
            MetricSample original = call.getArgument(0);
            MetricSample restored = roundTrip(original);
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

    @Test
    void capturedBoardRegionsTraverseReceiverStorageAndExistingHttpRoute() throws Exception {
        MqttAgentSubscriber receiver = new MqttAgentSubscriber(mock(MqttProperties.class), mock(AgentMessageHandler.class),
                handler, mock(TelemetryHeartbeatMessageHandler.class), mock(CommandResultMessageHandler.class),
                mock(RecoveryEventMessageHandler.class),
                mock(RecoveryEventBackfillService.class), CLOCK);
        for (String region : List.of("drive_maps", "drive_paths", "drive_search_nodes", "drive_odometry")) {
            ObjectNode message = fixture("board-app-memory-" + region + ".json");
            receiver.messageArrived(topic(message), new MqttMessage(mapper.writeValueAsBytes(message)));
        }
        assertThat(rows).hasSize(4);
        assertThat(events).hasSize(4);
        MetricSample first = rows.values().iterator().next();
        assertThat(first.getTelemetry().publisherInstanceId()).isEqualTo("bb9a8dd0-7d05-48ba-9c5e-06ea69c0d1c1");
        assertThat(first.getTelemetry().sourceSampleSeq()).isEqualTo("1510");
        assertThat(first.getTelemetry().observedUptimeMs()).isEqualTo("302303");
        assertThat(first.getTelemetry().context().executionId()).isEqualTo("e8d9b94d-cbfd-47ac-97bb-d41db2dd7118");
        assertThat(first.getTelemetry().collection().sampleId()).isEqualTo("bb9a8dd0-7d05-48ba-9c5e-06ea69c0d804");
        assertThat(first.getTelemetry().sentAt()).isEqualTo("2026-11-04T05:55:56.057Z");
        // Observed_at retains the established envelope UTC/fallback meaning; source time is explicit, not refreshed.
        assertThat(first.getObservedAt()).isEqualTo(Instant.parse("2026-11-04T05:55:56.057Z"));
        for (MetricSample sample : rows.values()) {
            assertThat(metric(sample, "memory.app.used_bytes").rawValue()).isEqualTo("0");
            assertThat(metric(sample, "memory.app.used_bytes").status()).isEqualTo("ok");
            assertThat(metric(sample, "memory.app.used_bytes").sourceRc()).isZero();
        }
        String body = mvc.perform(get("/api/v1/vms/{vmId}/metrics", first.getVmId())
                        .param("source", "zephyr_telemetry").param("names", "memory.app.used_bytes"))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        JsonNode response = mapper.readTree(body);
        assertThat(response.path("items")).hasSize(4);
        assertThat(response.path("items").findValuesAsText("region"))
                .containsExactlyInAnyOrder("drive_maps", "drive_paths", "drive_search_nodes", "drive_odometry");
        for (JsonNode item : response.path("items")) {
            assertThat(item.path("observed_at").isTextual()).isTrue();
            assertThat(Instant.parse(item.path("observed_at").asText())).isBefore(NOW);
            assertThat(item.path("received_at").asText()).isEqualTo(NOW.toString());
            assertThat(item.path("telemetry").path("source_sample_seq").asText()).isEqualTo("1510");
            assertThat(item.path("telemetry").path("context").path("receipt_id").asText())
                    .isEqualTo("88a52ab5-559c-4bdc-aed8-831722f4bbaa");
            assertThat(item.path("values").get(0).path("raw_value").asText()).isEqualTo("0");
        }
    }

    @Test
    void fullBusyAndReleasedRemainDistinctAndPeakSurvivesReturn() throws Exception {
        MetricSample empty = ingest(fixture("app-memory-empty.json"), NOW.minusSeconds(8));
        MetricSample full = ingest(fixture("app-memory-full.json"), NOW.minusSeconds(6));
        MetricSample busy = ingest(fixture("app-memory-busy.json"), NOW.minusSeconds(4));
        MetricSample released = ingest(fixture("app-memory-released.json"), NOW.minusSeconds(2));
        assertThat(metric(empty, "memory.app.used_bytes").rawValue()).isEqualTo("0");
        assertThat(metric(full, "memory.app.used_bytes").rawValue()).isEqualTo("80472");
        assertThat(metric(full, "memory.app.capacity_failure_total").counterValue()).isEqualTo("1");
        MetricValue unknown = metric(busy, "memory.app.used_bytes");
        assertThat(unknown.value()).isNull();
        assertThat(unknown.rawValue()).isNull();
        assertThat(unknown.status()).isEqualTo("collection_failed");
        assertThat(unknown.unavailableReason()).isEqualTo("owner_busy");
        assertThat(unknown.sourceRc()).isEqualTo(-16);
        assertThat(metric(released, "memory.app.used_bytes").rawValue()).isEqualTo("0");
        assertThat(metric(released, "memory.app.high_water_bytes").rawValue()).isEqualTo("80472");
        assertThat(metric(released, "memory.app.capacity_failure_total").rawValue()).isEqualTo("1");
        mvc.perform(get("/api/v1/vms/{vmId}/metrics", released.getVmId())
                        .param("names", "memory.app.used_bytes").param("to", NOW.plusSeconds(1).toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[2].values[0].value").isEmpty())
                .andExpect(jsonPath("$.items[2].values[0].unavailable_reason").value("owner_busy"))
                .andExpect(jsonPath("$.items[2].values[0].source_rc").value(-16))
                .andExpect(jsonPath("$.items[3].values[0].raw_value").value("0"));
    }

    @Test
    void gaugesAndCountersRetainUint64AboveTheDoubleSafeRange() throws Exception {
        ObjectNode message = fixture("app-memory-full.json");
        for (JsonNode node : message.path("payload").path("samples")) {
            ((ObjectNode) node).put("value", "18446744073709551615");
        }
        MetricSample sample = ingest(message, NOW.minusSeconds(1));
        assertThat(sample.getValues()).allSatisfy(value -> {
            assertThat(value.rawValue()).isEqualTo("18446744073709551615");
            assertThat(value.value()).isNull();
            assertThat(value.unavailableReason()).isNull();
            assertThat(value.status()).isEqualTo("ok");
        });
        assertThat(metric(sample, "memory.app.used_bytes").counterValue()).isNull();
        assertThat(metric(sample, "memory.app.contention_total").counterValue()).isEqualTo("18446744073709551615");
        mvc.perform(get("/api/v1/vms/{vmId}/metrics", sample.getVmId()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].values[1].raw_value").value("18446744073709551615"))
                .andExpect(jsonPath("$.items[0].values[1].value").isEmpty())
                .andExpect(jsonPath("$.items[0].values[1].unavailable_reason").isEmpty());
    }

    @ParameterizedTest
    @ValueSource(strings = {"18446744073709551616", "-1", "1.5", "01", "NaN", "Infinity"})
    void malformedUint64IsUnavailableNotZeroOrARoundedSuccess(String badValue) throws Exception {
        ObjectNode message = fixture("app-memory-full.json");
        ((ObjectNode) message.path("payload").path("samples").get(1)).put("value", badValue);
        MetricValue value = metric(ingest(message, NOW), "memory.app.used_bytes");
        assertThat(value.rawValue()).isNull();
        assertThat(value.value()).isNull();
        assertThat(value.unavailableReason()).isEqualTo("parse_error");
        assertThat(value.status()).isEqualTo("ok"); // Original status is evidence, not a repaired provider report.
    }

    @Test
    void repeatedAndOutOfOrderRegionMessagesNeverRelabelTheOriginalObservation() throws Exception {
        ObjectNode maps = fixture("board-app-memory-drive_maps.json");
        MetricSample first = ingest(maps, NOW.minusSeconds(10));
        assertThat(handler.handle(topic(maps), mapper.writeValueAsBytes(maps), NOW)).isEqualTo(AgentMessageHandler.Result.IGNORED);
        ObjectNode paths = fixture("board-app-memory-drive_paths.json");
        MetricSample second = ingest(paths, NOW);
        assertThat(first.getReceivedAt()).isEqualTo(NOW.minusSeconds(10));
        assertThat(first.getTelemetry().sentUptimeMs()).isEqualTo("303629");
        assertThat(second.getTelemetry().sentUptimeMs()).isEqualTo("304075");
        ObjectNode late = fixture("app-memory-empty.json");
        MetricSample delayed = ingest(late, NOW.plusSeconds(1));
        assertThat(delayed.getTelemetry().sourceSampleSeq()).isEqualTo("2");
        assertThat(delayed.getTelemetry().observedUptimeMs()).isEqualTo("1210");
        assertThat(rows).hasSize(3);
        assertThat(events).hasSize(3);
    }

    @Test
    void cachedFirmwareTimesAreKeptSeparateFromTheDirectHeapCollection() throws Exception {
        MetricSample sample = ingest(fixture("board-memory.json"), NOW);
        assertThat(sample.getTelemetry().family()).isEqualTo("memory");
        assertThat(sample.getTelemetry().observedUptimeMs()).isEqualTo("302704");
        assertThat(sample.getTelemetry().collection().startUptimeMs()).isNotEqualTo("302704");
        assertThat(metric(sample, "memory.firmware.mapped_bytes").rawValue()).isEqualTo("2252800");
        assertThat(metric(sample, "memory.page_allocator.free_bytes").rawValue()).isEqualTo("14524416");
        ObjectNode old = fixture("board-memory.json");
        old.put("message_id", "legacy-memory-without-source-pair");
        ((ObjectNode) old.path("payload")).remove(List.of("source_sample_seq", "observed_uptime_ms"));
        MetricSample legacy = ingest(old, NOW);
        assertThat(legacy.getTelemetry().sourceSampleSeq()).isNull();
        assertThat(legacy.getTelemetry().observedUptimeMs()).isNull();
        assertThat(legacy.getTelemetry().collection()).isEqualTo(sample.getTelemetry().collection());
        assertThat(legacy.getTelemetry().context()).isEqualTo(sample.getTelemetry().context());
        assertThat(metric(legacy, "memory.kernel_heap.used_bytes").value()).isZero();
    }

    @Test
    void cachedSourceMayBeObservedDuringCollectionButNotAfterIt() throws Exception {
        ObjectNode message = fixture("app-memory-full.json");
        ((ObjectNode) message.path("payload")).put("observed_uptime_ms", "2300");
        MetricSample sample = ingest(message, NOW);
        assertThat(sample.getTelemetry().collection().startUptimeMs()).isEqualTo("2280");
        assertThat(sample.getTelemetry().observedUptimeMs()).isEqualTo("2300");
    }

    @ParameterizedTest
    @ValueSource(strings = {"unsupported", "sample_expired"})
    void absentAndExpiredSourceRetainReasonsAndReturnCodesInsteadOfSuccess(String reason) throws Exception {
        ObjectNode message = fixture("app-memory-busy.json");
        for (JsonNode node : message.path("payload").path("samples")) {
            ObjectNode value = (ObjectNode) node;
            value.put("status", reason.equals("unsupported") ? "unsupported" : "collection_failed");
            value.put("reason", reason);
            value.put("source_rc", reason.equals("unsupported") ? -95 : -110);
        }
        MetricValue value = metric(ingest(message, NOW), "memory.app.used_bytes");
        assertThat(value.rawValue()).isNull();
        assertThat(value.value()).isNull();
        assertThat(value.unavailableReason()).isEqualTo(reason);
        assertThat(value.sourceRc()).isEqualTo(reason.equals("unsupported") ? -95 : -110);
    }

    @Test
    void malformedOptionalMemoryPairIsRejectedWithoutRejectingOlderMessages() throws Exception {
        ObjectNode message = fixture("board-memory.json");
        ((ObjectNode) message.path("payload")).remove("observed_uptime_ms");
        assertThat(handler.handle(topic(message), mapper.writeValueAsBytes(message), NOW))
                .isEqualTo(AgentMessageHandler.Result.REJECTED);
        verify(repository, never()).save(any());
        ObjectNode legacy = fixture("board-memory.json");
        ((ObjectNode) legacy.path("payload")).remove(List.of("observed_uptime_ms", "source_sample_seq"));
        var telemetry = ingest(legacy, NOW).getTelemetry();
        assertThat(telemetry.sourceSampleSeq()).isNull();
        assertThat(telemetry.observedUptimeMs()).isNull();
        assertThat(telemetry.collection().endUptimeMs()).isEqualTo(legacy.path("payload").path("collection").path("end_uptime_ms").asText());
        assertThat(telemetry.publisherInstanceId()).isEqualTo(legacy.path("publisher_instance_id").asText());
    }

    @Test
    void malformedSourcePairsFutureUptimeMixedRegionAndBindingAreRejectedThenNormalUseRecovers() throws Exception {
        List<Consumer<ObjectNode>> corruptions = List.of(
                root -> ((ObjectNode) root.path("payload")).remove("source_sample_seq"),
                root -> ((ObjectNode) root.path("payload")).put("source_sample_seq", "-1"),
                root -> ((ObjectNode) root.path("payload")).put("observed_uptime_ms", "18446744073709551615"),
                root -> ((ObjectNode) root.path("payload").path("collection")).put("start_uptime_ms", "3000"),
                root -> ((ObjectNode) root.path("clock")).put("sent_uptime_ms", "1000"),
                root -> ((ObjectNode) root.path("clock")).put("sent_at", "not-UTC"),
                root -> ((ObjectNode) root.path("payload").path("samples").get(1).path("labels")).put("region", "drive_paths"),
                root -> ((ObjectNode) root.path("context")).put("binding", "host_provisioned"));
        for (Consumer<ObjectNode> corrupt : corruptions) {
            ObjectNode message = fixture("app-memory-full.json");
            corrupt.accept(message);
            assertThat(handler.handle(topic(message), mapper.writeValueAsBytes(message), NOW))
                    .isEqualTo(AgentMessageHandler.Result.REJECTED);
        }
        ObjectNode wrongPublisher = fixture("app-memory-full.json");
        wrongPublisher.put("publisher_instance_id", "not-a-uuid");
        assertThat(handler.handle(topic(wrongPublisher), mapper.writeValueAsBytes(wrongPublisher), NOW))
                .isEqualTo(AgentMessageHandler.Result.REJECTED);
        verify(repository, never()).save(any());
        ingest(fixture("app-memory-full.json"), NOW);
        assertThat(rows).hasSize(1);
    }

    @Test
    void fixtureProvenanceHashesMatchTheExactCopiedProviderBytes() throws Exception {
        JsonNode manifest = mapper.readTree(bytes("provenance.json"));
        for (JsonNode entry : manifest.path("files")) {
            assertThat(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes(entry.path("file").asText()))))
                    .as(entry.path("file").asText()).isEqualTo(entry.path("sha256").asText());
        }
    }

    @Test
    void legacyArrayAndObjectStorageRemainReadableWithoutNewMetadata() {
        MetricValuesConverter converter = new MetricValuesConverter();
        MetricSampleData legacy = converter.convertToEntityAttribute("[{\"name\":\"cpu_percent\",\"value\":3.5,\"unit\":\"percent\",\"labels\":{},\"unavailableReason\":null}]");
        assertThat(legacy.telemetry()).isNull();
        assertThat(legacy.values().getFirst().value()).isEqualTo(3.5);
        assertThat(legacy.values().getFirst().rawValue()).isNull();
        MetricSampleData previous = converter.convertToEntityAttribute("{\"values\":[],\"network\":{\"publisherInstanceId\":\"legacy\",\"observedUptimeMs\":\"12\"},\"cpuWindow\":null}");
        assertThat(previous.telemetry()).isNull();
        assertThat(previous.network().observedUptimeMs()).isEqualTo("12");
    }

    private MetricSample ingest(ObjectNode message, Instant receivedAt) throws Exception {
        assertThat(handler.handle(topic(message), mapper.writeValueAsBytes(message), receivedAt))
                .isEqualTo(AgentMessageHandler.Result.ACCEPTED);
        return rows.values().stream().filter(sample -> sample.getReceivedAt().equals(receivedAt))
                .reduce((first, second) -> second).orElseThrow();
    }

    private static MetricValue metric(MetricSample sample, String name) {
        return sample.getValues().stream().filter(value -> name.equals(value.name())).findFirst().orElseThrow();
    }

    private static MetricSample roundTrip(MetricSample sample) {
        MetricValuesConverter converter = new MetricValuesConverter();
        MetricSampleData original = new MetricSampleData(sample.getValues(), sample.getNetwork(), sample.getCpuWindow(), sample.getTelemetry());
        return new MetricSample(sample.getSampleId(), sample.getVmId(), sample.getBootId(), sample.getSource(),
                sample.getObservedAt(), sample.getReceivedAt(), sample.getWindowMs(),
                converter.convertToEntityAttribute(converter.convertToDatabaseColumn(original)));
    }

    private ObjectNode fixture(String name) throws IOException {
        return (ObjectNode) mapper.readTree(bytes(name));
    }

    private static byte[] bytes(String name) throws IOException {
        try (var stream = VmTelemetryAppMemoryTest.class.getResourceAsStream("/telemetry-app-memory/" + name)) {
            if (stream == null) {
                throw new IOException("missing fixture " + name);
            }
            return stream.readAllBytes();
        }
    }

    private static String topic(JsonNode message) {
        return "xenova/v2/vms/" + message.path("vm_id").asText() + "/telemetry/metrics";
    }
}

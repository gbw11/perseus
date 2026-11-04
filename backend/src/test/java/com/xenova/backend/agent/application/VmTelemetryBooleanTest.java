package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleData;
import com.xenova.backend.agent.domain.MetricValuesConverter;
import com.xenova.backend.metrics.api.MetricsController;
import com.xenova.backend.metrics.application.MetricsQueryService;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

/** Exercises the public ingest path, stored JSON representation and existing metrics response. */
class VmTelemetryBooleanTest {
    private static final String VM = "f1000000-0000-4000-8000-000000000001";
    private static final Instant NOW = Instant.parse("2026-11-04T00:00:00Z");
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
    private final AgentDataService data = mock(AgentDataService.class);
    private final VmTelemetryMessageHandler handler =
            new VmTelemetryMessageHandler(mapper, data, mock(VmRecordRepository.class));

    @ParameterizedTest
    @MethodSource("booleanSamples")
    void convertsBothBooleanValuesForEachIndicator(String name, String family, boolean flag) throws Exception {
        MetricSample sample = ingest(family, metric(name, Boolean.toString(flag), "ok", "null"));
        var value = stored(sample).getValues().getFirst();
        assertThat(value.value()).isEqualTo(flag ? 1.0 : 0.0);
        assertThat(value.unit()).isEqualTo("bool");
        if (name.startsWith("network.")) {
            assertThat(value.labels()).containsEntry("interface", "eth0");
        } else {
            assertThat(value.labels()).isEmpty();
        }
        assertThat(value.unavailableReason()).isNull();
    }

    private static Stream<Arguments> booleanSamples() {
        return Stream.of("network.interface_up", "network.carrier", "loop.in_flight")
                .flatMap(name -> Stream.of(true, false)
                        .map(flag -> Arguments.of(name, name.startsWith("network.") ? "network" : "loop_progress", flag)));
    }

    @Test
    void falseAndUnavailableRemainDistinctThroughStorageAndTheMetricsRoute() throws Exception {
        MetricSample sample = stored(ingest("network",
                metric("network.interface_up", "false", "ok", "null") + ","
                + metric("network.carrier", "null", "unsupported", "\"not_supported\"")));
        when(data.findMetricSamples(eq(VM), any(), any())).thenReturn(List.of(sample));
        MockMvcBuilders.standaloneSetup(new MetricsController(
                        new MetricsQueryService(data, Clock.fixed(NOW, ZoneOffset.UTC))))
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper))
                .build().perform(get("/api/v1/vms/{vmId}/metrics", VM))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].observed_at").isString())
                .andExpect(jsonPath("$.items[0].observed_at").value(NOW.toString()))
                .andExpect(jsonPath("$.items[0].received_at").value(NOW.toString()))
                .andExpect(jsonPath("$.items[0].values[0].value").value(0.0))
                .andExpect(jsonPath("$.items[0].values[0].unavailable_reason").isEmpty())
                .andExpect(jsonPath("$.items[0].values[1].value").isEmpty())
                .andExpect(jsonPath("$.items[0].values[1].unavailable_reason").value("not_supported"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "\"true\"", "\"garbage\"", "{}", "[]", "\"NaN\"", "\"Infinity\"", "\"-Infinity\""})
    void invalidOkSamplesHaveParseErrorInsteadOfOkOrFalse(String rawValue) throws Exception {
        var value = stored(ingest("network",
                metric("network.carrier", rawValue, "ok", "null"))).getValues().getFirst();
        assertThat(value.value()).isNull();
        assertThat(value.unavailableReason()).isEqualTo("parse_error");
    }

    @ParameterizedTest
    @ValueSource(strings = {"unsupported", "collection_failed", "unavailable"})
    void unsuccessfulCollectionNeverBecomesAValidBoolean(String sourceStatus) throws Exception {
        var value = stored(ingest("loop_progress",
                metric("loop.in_flight", "false", sourceStatus, "null"))).getValues().getFirst();
        assertThat(value.value()).isNull();
        assertThat(value.unavailableReason()).isEqualTo(sourceStatus);
    }

    @Test
    void preservesExistingNumbersAndDecimalStrings() throws Exception {
        MetricSample sample = stored(ingest("memory", """
                {"name":"memory.kernel_heap.used_bytes","unit":"bytes","encoding":"u64_decimal",
                 "value":"65536","status":"ok","reason":null,"labels":{}},
                {"name":"cpu.system.percent","unit":"percent","encoding":"float64",
                 "value":12.5,"status":"ok","reason":null,"labels":{}},
                {"name":"publisher.collection_failures_total","unit":"count","encoding":"u64_decimal",
                 "value":"0","status":"ok","reason":null,"labels":{}}
                """));
        assertThat(sample.getValues()).extracting(value -> value.value()).containsExactly(65536.0, 12.5, 0.0);
        assertThat(sample.getValues()).allSatisfy(value -> assertThat(value.unavailableReason()).isNull());
    }

    private MetricSample ingest(String family, String samples) {
        when(data.recordMetrics(any())).thenReturn(true);
        String message = """
                {"schema_version":"2.1","type":"telemetry.metrics","message_id":"%s/boot/publisher/metrics/1",
                 "vm_id":"%s","boot_id":"boot","clock":{"sent_at":null},
                 "payload":{"family":"%s","samples":[%s]}}
                """.formatted(VM, VM, family, samples);
        assertThat(handler.handle("xenova/v2/vms/" + VM + "/telemetry/metrics",
                message.getBytes(StandardCharsets.UTF_8), NOW)).isEqualTo(Result.ACCEPTED);
        var capture = ArgumentCaptor.forClass(MetricSample.class);
        verify(data).recordMetrics(capture.capture());
        return capture.getValue();
    }

    private static String metric(String name, String value, String status, String reason) {
        String labels = name.startsWith("network.") ? "{\"interface\":\"eth0\"}" : "{}";
        return """
                {"name":"%s","unit":"bool","kind":"gauge","encoding":"boolean","value":%s,
                 "status":"%s","reason":%s,"labels":%s}
                """.formatted(name, value, status, reason, labels);
    }

    private static MetricSample stored(MetricSample sample) {
        var converter = new MetricValuesConverter();
        var data = new MetricSampleData(sample.getValues(), sample.getNetwork(), sample.getCpuWindow());
        return new MetricSample(sample.getSampleId(), sample.getVmId(), sample.getBootId(), sample.getSource(),
                sample.getObservedAt(), sample.getReceivedAt(), sample.getWindowMs(),
                converter.convertToEntityAttribute(converter.convertToDatabaseColumn(data)));
    }
}

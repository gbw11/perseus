package com.xenova.backend.display;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;

/** Opt-in real Python feed/MQTT + Spring REST/WS + two browser DOMs. Synthetic guests only. */
@EnabledIfEnvironmentVariable(named = "DISPLAY_PIPELINE_PYTHON", matches = ".+")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class DisplayPipelineIntegrationTest {
    static final ObjectMapper JSON = new ObjectMapper();
    static final String SERVICE_TOKEN = UUID.randomUUID().toString();
    static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    static Process feed;
    static String base;
    static String vmId;
    static Path output;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) throws Exception {
        Path repo = Path.of("..").toAbsolutePath().normalize();
        output = Path.of(System.getenv("DISPLAY_PIPELINE_OUTPUT")).toAbsolutePath();
        Files.createDirectories(output);
        feed = new ProcessBuilder(System.getenv("DISPLAY_PIPELINE_PYTHON"), "-u", "-B",
                repo.resolve("platform/services/display/router/tests/pipeline_server.py").toString(), output.toString())
                .redirectError(output.resolve("feed.log").toFile()).start();
        feed.getOutputStream().write(JSON.writeValueAsBytes(Map.of("token", SERVICE_TOKEN)));
        feed.getOutputStream().write('\n');
        feed.getOutputStream().flush();
        // Bound startup and terminate a failed child even when the Spring context cannot start.
        try {
            var reader = new BufferedReader(new InputStreamReader(feed.getInputStream()));
            var line = java.util.concurrent.CompletableFuture.supplyAsync(() -> {
                try { return reader.readLine(); } catch (Exception e) { throw new RuntimeException(e); }
            }).get(30, TimeUnit.SECONDS);
            JsonNode ready = JSON.readTree(line);
            base = ready.path("base_url").asText();
            vmId = ready.path("vm_id").asText();
        } catch (Exception error) {
            stop();
            throw error;
        }
        registry.add("xenova.display.enabled", () -> true);
        registry.add("xenova.display.base-url", () -> base);
        registry.add("xenova.display.service-token", () -> SERVICE_TOKEN);
    }

    @LocalServerPort int port;
    @Autowired TestRestTemplate rest;

    @AfterAll static void stop() throws Exception {
        if (feed == null) return;
        feed.getOutputStream().close(); // stdin EOF closes browser, feed and broker gracefully.
        if (!feed.waitFor(20, TimeUnit.SECONDS)) {
            feed.destroy();
            if (!feed.waitFor(5, TimeUnit.SECONDS)) feed.destroyForcibly().waitFor();
        }
    }

    @Test void sameSourceReachesSpringRestWebsocketAndBothLocalScreens() throws Exception {
        var cases = new ArrayList<Map<String, Object>>();
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        String credentials = "{\"username\":\"pipeline-user\",\"password\":\"password123!\"}";
        rest.postForEntity("/api/v1/auth/signup", new HttpEntity<>(credentials, headers), String.class);
        String token = JSON.readTree(rest.postForEntity("/api/v1/auth/login",
                new HttpEntity<>(credentials, headers), String.class).getBody()).path("access_token").asText();
        headers.setBearerAuth(token);
        assertThat(rest.getForEntity("/api/v1/display/state", String.class).getStatusCode().value()).isEqualTo(401);
        assertThat(HTTP.send(HttpRequest.newBuilder(URI.create(base + "/api/v1/display/state")).GET().build(),
                HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(401);

        try (var socket = new DisplayIntegrationTest.Client()) {
            socket.session = new StandardWebSocketClient().execute(socket,
                    "ws://127.0.0.1:" + port + "/ws/v1/display/state").get(5, TimeUnit.SECONDS);
            socket.send(JSON.writeValueAsString(Map.of("type", "auth", "access_token", token)));
            assertThat(JSON.readTree(socket.next()).path("type").asText()).isEqualTo("authenticated");
            for (String scenario : List.of("healthy", "error", "stale", "recovered", "paused", "stopped", "xen-unavailable", "recovered")) {
                call("/test/scenario/" + scenario, "POST");
                JsonNode presentation = call("/api/v1/display/presentation", "GET");
                JsonNode expected = presentation.path("state");
                String kind = switch (scenario) {
                    case "healthy", "recovered" -> "status";
                    case "error" -> "app_error";
                    case "stale" -> "app_waiting";
                    case "xen-unavailable" -> "stale";
                    default -> scenario;
                };
                assertThat(presentation.path("decisions").path(vmId).path("kind").asText()).isEqualTo(kind);
                var response = rest.exchange("/api/v1/display/state", HttpMethod.GET, new HttpEntity<>(headers), String.class);
                assertThat(response.getStatusCode().value()).isEqualTo(200);
                JsonNode external = JSON.readTree(response.getBody());
                assertSameSource(expected, external);
                // Ignore queued frames from the previous scenario; require the current source observation.
                JsonNode streamed = null;
                long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(12);
                while (System.nanoTime() < deadline) {
                    JsonNode frame = JSON.readTree(socket.next());
                    if (frame.path("type").asText().equals("display.state") &&
                            sameSource(expected, frame.path("state"))) {
                        streamed = frame.path("state");
                        break;
                    }
                }
                assertThat(streamed).as("fresh WS snapshot for " + scenario).isNotNull();
                JsonNode rendered = call("/test/browser/" + kind, "POST");
                assertSameSource(expected, rendered.path("state"));
                assertThat(rendered.path("dom_rendered").size()).isEqualTo(2);
                assertThat(rendered.path("physical_hdmi_verified").asBoolean()).isFalse();
                assertThat(expected.path("vms").get(2).path("os").path("observed").isNull()).isTrue();
                assertThat(expected.path("drive").path("mode").asText()).isEqualTo("unknown");
                assertThat(JSON.writeValueAsString(external)).doesNotContain(SERVICE_TOKEN);
                cases.add(Map.of("scenario", scenario, "decision", kind, "state", external,
                        "websocket_state", streamed, "render", rendered));
            }
            // A broken Spring->feed path must not break local HDMI presentation consumption.
            call("/test/scenario/upstream-down", "POST");
            assertThat(rest.exchange("/api/v1/display/state", HttpMethod.GET, new HttpEntity<>(headers), String.class)
                    .getStatusCode().value()).isEqualTo(503);
            boolean unavailable = false;
            for (int i = 0; i < 8 && !unavailable; i++)
                unavailable = JSON.readTree(socket.next()).path("type").asText().equals("display.unavailable");
            assertThat(unavailable).isTrue();
            call("/test/browser/status", "POST");
            call("/test/scenario/upstream-up", "POST");
            boolean recovered = false;
            for (int i = 0; i < 8 && !recovered; i++)
                recovered = JSON.readTree(socket.next()).path("type").asText().equals("display.state");
            assertThat(recovered).isTrue();
            assertThat(rest.exchange("/api/v1/display/state", HttpMethod.GET, new HttpEntity<>(headers), String.class)
                    .getStatusCode().value()).isEqualTo(200);
            socket.send("{\"type\":\"input\",\"keys\":[\"w\"]}");
            assertThat(socket.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(1008);
        }
        var evidence = Map.of("passed", true, "completed_at", Instant.now().toString(),
                "scope", "real loopback MQTT -> Python feed -> Spring REST/WS and two headless browser DOMs; synthetic guests and clock",
                "physical_hdmi_verified", false, "guest_modified", false,
                "checks", List.of("jwt_and_service_auth", "same_source_in_both_consumers", "two_dom_render_acks",
                        "spring_upstream_failure_explicit_and_recovers", "local_presentation_independent_of_spring_path",
                        "read_only_ws_rejects_input", "debian_inventory_not_invented", "no_mode_inference"), "cases", cases);
        Files.writeString(output.resolve("pipeline-evidence.json"), JSON.writerWithDefaultPrettyPrinter().writeValueAsString(evidence) + "\n");
    }

    static JsonNode call(String path, String method) throws Exception {
        var request = HttpRequest.newBuilder(URI.create(base + path)).timeout(Duration.ofSeconds(35))
                .header("Authorization", "Bearer " + SERVICE_TOKEN);
        if (method.equals("POST")) request.POST(HttpRequest.BodyPublishers.noBody()); else request.GET();
        var response = HTTP.send(request.build(), HttpResponse.BodyHandlers.ofString());
        assertThat(response.statusCode()).as(path + ": " + response.body()).isEqualTo(200);
        return JSON.readTree(response.body());
    }

    static boolean sameSource(JsonNode expected, JsonNode actual) {
        // Poll timestamps and ages can move between reads; publisher sequences must still match.
        return expected.path("source_epoch").equals(actual.path("source_epoch"))
                && stable(expected.path("vms")).equals(stable(actual.path("vms")))
                && stable(expected.path("drive")).equals(stable(actual.path("drive")))
                && expected.path("binding_scope").equals(actual.path("binding_scope"));
    }

    static JsonNode stable(JsonNode input) {
        JsonNode copy = input.deepCopy();
        if (copy.isObject()) {
            var object = (com.fasterxml.jackson.databind.node.ObjectNode) copy;
            object.remove(List.of("age_ms", "received_at", "observed_at"));
            var names = new ArrayList<String>();
            object.fieldNames().forEachRemaining(names::add);
            for (String name : names) object.set(name, stable(object.get(name)));
        } else if (copy.isArray()) {
            var array = (com.fasterxml.jackson.databind.node.ArrayNode) copy;
            for (int index = 0; index < array.size(); index++) array.set(index, stable(array.get(index)));
        }
        return copy;
    }

    static void assertSameSource(JsonNode expected, JsonNode actual) {
        assertThat(sameSource(expected, actual)).as("same VM UUIDs, source observations, run IDs, states and drive data").isTrue();
    }
}

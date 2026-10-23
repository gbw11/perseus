package com.xenova.backend.display;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.common.error.ApiException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

/** Read-only adapter. The service credential never appears in the browser response. */
@Component
public class DisplayFeedClient {
    private final ObjectMapper json;
    private final boolean enabled;
    private final String baseUrl;
    private final String token;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();

    public DisplayFeedClient(ObjectMapper json,
            @Value("${xenova.display.enabled:false}") boolean enabled,
            @Value("${xenova.display.base-url:http://127.0.0.1:8082}") String baseUrl,
            @Value("${xenova.display.service-token:}") String token) {
        this.json = json;
        this.enabled = enabled;
        this.baseUrl = baseUrl;
        this.token = token;
    }

    public JsonNode snapshot() {
        if (!enabled || token == null || token.isBlank()) throw unavailable();
        try {
            HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl.replaceAll("/$", "") + "/api/v1/display/state"))
                    .timeout(Duration.ofSeconds(2)).header("Authorization", "Bearer " + token).GET().build();
            // The collector emits a fixed three-VM snapshot; reject oversized or incompatible responses.
            HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200 || response.body().length() > 65536) throw unavailable();
            JsonNode state = json.readTree(response.body());
            if (state == null || state.path("schema_version").asInt() != 1
                    || !"display.state".equals(state.path("type").asText()) || !state.path("vms").isArray()
                    || state.path("vms").size() != 3 || !state.path("source_epoch").isTextual()
                    || !state.path("sequence").isIntegralNumber() || !state.path("drive").isObject()) {
                throw unavailable();
            }
            return state;
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw unavailable();
        } catch (Exception exception) {
            throw unavailable();
        }
    }

    private static ApiException unavailable() {
        return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "DISPLAY_FEED_UNAVAILABLE", "표시 상태 수집기에 연결할 수 없습니다.");
    }
}

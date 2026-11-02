package com.xenova.backend.event.infrastructure;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.event.application.RecoveryEvent;
import com.xenova.backend.vm.infrastructure.Dom0ManagerProperties;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpHeaders;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.converter.json.AbstractJackson2HttpMessageConverter;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Component
public class RecoveryEventsClient {

    private final RestClient client;

    public RecoveryEventsClient(Dom0ManagerProperties properties, ObjectMapper objectMapper) {
        this.client = createClient(properties, objectMapper);
    }

    public Page fetch(String afterEventId, int limit) {
        try {
            Page page = client.get()
                    .uri(builder -> {
                        builder.path("/api/v1/recovery/events").queryParam("limit", limit);
                        if (StringUtils.hasText(afterEventId)) {
                            builder.queryParam("after_event_id", afterEventId);
                        }
                        return builder.build();
                    })
                    .retrieve()
                    .onStatus(status -> status.isError(), (request, response) -> {
                        throw new RecoveryEventsClientException(response.getStatusCode().value());
                    })
                    .body(Page.class);
            if (page == null || page.events() == null) {
                throw new RecoveryEventsClientException(502);
            }
            return page;
        } catch (RecoveryEventsClientException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new RecoveryEventsClientException(503, exception);
        }
    }

    private static RestClient createClient(Dom0ManagerProperties properties, ObjectMapper objectMapper) {
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(properties.connectTimeout())
                .build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(properties.queryTimeout());
        RestClient.Builder builder = RestClient.builder()
                .baseUrl(properties.baseUrl())
                .requestFactory(requestFactory)
                .messageConverters(converters -> {
                    converters.removeIf(AbstractJackson2HttpMessageConverter.class::isInstance);
                    converters.add(0, new MappingJackson2HttpMessageConverter(objectMapper));
                })
                .defaultHeader(HttpHeaders.ACCEPT, "application/json");
        if (StringUtils.hasText(properties.serviceToken())) {
            builder.defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + properties.serviceToken());
        }
        return builder.build();
    }

    public record Page(
            List<RecoveryEvent> events,
            UUID nextAfterEventId,
            UUID oldestEventId,
            boolean gap) {
    }

    public static final class RecoveryEventsClientException extends RuntimeException {

        private final int status;

        public RecoveryEventsClientException(int status) {
            this(status, null);
        }

        public RecoveryEventsClientException(int status, Throwable cause) {
            super("Recovery events request failed with status " + status, cause);
            this.status = status;
        }

        public int getStatus() {
            return status;
        }

        public boolean isRetryable() {
            return status == 503 || status == 504;
        }
    }
}

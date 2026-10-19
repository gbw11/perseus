package com.perseus.backend.vm.infrastructure;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.vm.domain.CreateVmCommand;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import java.io.IOException;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.net.http.HttpClient;
import java.net.http.HttpConnectTimeoutException;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.TimeoutException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Component
public class Dom0ManagerClient implements VmControlPort {

    private static final Logger log = LoggerFactory.getLogger(Dom0ManagerClient.class);

    private static final int MAX_ATTEMPTS = 2;

    private final RestClient queryClient;
    private final RestClient commandClient;
    private final ObjectMapper objectMapper;
    private final Duration retryBackoff;

    public Dom0ManagerClient(Dom0ManagerProperties properties, ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
        this.retryBackoff = properties.retryBackoff();
        this.queryClient = createClient(properties, properties.queryTimeout());
        this.commandClient = createClient(properties, properties.commandTimeout());
    }

    @Override
    public List<Vm> list() {
        Vm[] response = executeQuery(() -> queryClient.get()
                .uri("/api/v1/vms")
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(Vm[].class));
        return response == null ? List.of() : Arrays.asList(response);
    }

    @Override
    public Vm get(String vmId) {
        return requireBody(executeQuery(() -> queryClient.get()
                .uri("/api/v1/vms/{vmId}", vmId)
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(Vm.class)));
    }

    @Override
    public Vm create(String vmId, CreateVmCommand command) {
        return requireBody(executeCommand(() -> commandClient.post()
                .uri("/api/v1/vms")
                .body(Dom0CreateVmRequest.of(vmId, command))
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(Vm.class)));
    }

    @Override
    public Vm start(String vmId) {
        return requireBody(executeCommand(() -> commandClient.post()
                .uri("/api/v1/vms/{vmId}/start", vmId)
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(Vm.class)));
    }

    @Override
    public Vm stop(String vmId) {
        return requireBody(executeCommand(() -> commandClient.post()
                .uri("/api/v1/vms/{vmId}/stop", vmId)
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(Vm.class)));
    }

    @Override
    public void destroy(String vmId) {
        executeCommand(() -> {
            commandClient.delete()
                    .uri("/api/v1/vms/{vmId}", vmId)
                    .retrieve()
                    .onStatus(status -> status.isError(), this::handleError)
                    .toBodilessEntity();
            return null;
        });
    }

    private RestClient createClient(Dom0ManagerProperties properties, Duration readTimeout) {
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(properties.connectTimeout())
                .build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(readTimeout);

        RestClient.Builder builder = RestClient.builder()
                .baseUrl(properties.baseUrl())
                .requestFactory(requestFactory)
                .messageConverters(converters -> {
                    converters.removeIf(MappingJackson2HttpMessageConverter.class::isInstance);
                    converters.add(new MappingJackson2HttpMessageConverter(objectMapper));
                })
                .defaultHeader(HttpHeaders.ACCEPT, "application/json");
        if (StringUtils.hasText(properties.serviceToken())) {
            builder.defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + properties.serviceToken());
        }
        return builder.build();
    }

    private void handleError(
            org.springframework.http.HttpRequest request,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        Dom0ErrorResponse error;
        try {
            error = objectMapper.readValue(response.getBody(), Dom0ErrorResponse.class);
        } catch (Exception exception) {
            throw new ApiException(
                    HttpStatus.BAD_GATEWAY,
                    "VM_CONTROL_BAD_RESPONSE",
                    "VM 제어 서비스의 오류 응답을 해석할 수 없습니다.");
        }

        if (StringUtils.hasText(error.detail())) {
            log.warn("dom0-manager error: code={}, detail={}", error.code(), error.detail());
        }
        throw mapError(error, response.getStatusCode().value());
    }

    private ApiException mapError(Dom0ErrorResponse error, int upstreamStatus) {
        String message = StringUtils.hasText(error.message())
                ? error.message()
                : "VM 제어 요청을 처리하지 못했습니다.";
        return switch (error.code() == null ? "" : error.code()) {
            case "VMNotFound" -> new ApiException(HttpStatus.NOT_FOUND, "VM_NOT_FOUND", message);
            case "VMExists" -> new ApiException(HttpStatus.CONFLICT, "VM_ALREADY_EXISTS", message);
            case "InvalidState" -> new ApiException(HttpStatus.CONFLICT, "INVALID_VM_STATE", message);
            case "InvalidArgument" -> new ApiException(HttpStatus.BAD_REQUEST, "INVALID_ARGUMENT", message);
            case "InsufficientResources" ->
                    new ApiException(HttpStatus.CONFLICT, "INSUFFICIENT_RESOURCES", message);
            case "DriverError" -> new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "VM_DRIVER_ERROR", message);
            default -> {
                log.warn("Unknown dom0-manager error code={}, status={}", error.code(), upstreamStatus);
                yield new ApiException(
                        HttpStatus.BAD_GATEWAY,
                        "VM_CONTROL_BAD_RESPONSE",
                        "VM 제어 서비스가 알 수 없는 오류를 반환했습니다.");
            }
        };
    }

    // Reads are safe to repeat, so any transport failure is retried once.
    private <T> T executeQuery(ClientCall<T> call) {
        return execute(call, true);
    }

    // A command that timed out may still be running in dom0-manager (single worker),
    // so only failures where the request never arrived are retried.
    private <T> T executeCommand(ClientCall<T> call) {
        return execute(call, false);
    }

    private <T> T execute(ClientCall<T> call, boolean retryAfterSend) {
        for (int attempt = 1; ; attempt++) {
            try {
                return call.execute();
            } catch (ApiException exception) {
                throw exception;
            } catch (ResourceAccessException exception) {
                TransportFailure failure = classify(exception);
                boolean retryable = failure == TransportFailure.CONNECT || retryAfterSend;
                if (!retryable || attempt >= MAX_ATTEMPTS) {
                    throw failure.toApiException();
                }
                log.warn("dom0-manager call failed ({}), retrying once", failure);
                pauseBeforeRetry();
            } catch (RestClientException exception) {
                throw new ApiException(
                        HttpStatus.BAD_GATEWAY,
                        "VM_CONTROL_BAD_RESPONSE",
                        "VM 제어 서비스의 응답을 처리할 수 없습니다.");
            }
        }
    }

    private TransportFailure classify(Throwable throwable) {
        Throwable current = throwable;
        while (current != null) {
            if (current instanceof HttpConnectTimeoutException || current instanceof ConnectException) {
                return TransportFailure.CONNECT;
            }
            if (current instanceof HttpTimeoutException
                    || current instanceof SocketTimeoutException
                    || current instanceof TimeoutException) {
                return TransportFailure.TIMEOUT;
            }
            current = current.getCause();
        }
        return TransportFailure.INTERRUPTED;
    }

    private void pauseBeforeRetry() {
        try {
            Thread.sleep(retryBackoff);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw TransportFailure.CONNECT.toApiException();
        }
    }

    private Vm requireBody(Vm vm) {
        if (vm == null) {
            throw new ApiException(
                    HttpStatus.BAD_GATEWAY,
                    "VM_CONTROL_BAD_RESPONSE",
                    "VM 제어 서비스가 응답 본문을 반환하지 않았습니다.");
        }
        return vm;
    }

    private enum TransportFailure {
        // The request never reached dom0-manager.
        CONNECT,
        // dom0-manager accepted the request but did not answer in time.
        TIMEOUT,
        // The connection broke after the request may have been sent.
        INTERRUPTED;

        ApiException toApiException() {
            if (this == TIMEOUT) {
                return new ApiException(
                        HttpStatus.GATEWAY_TIMEOUT,
                        "VM_CONTROL_TIMEOUT",
                        "VM 제어 서비스의 응답 시간이 초과되었습니다.");
            }
            return new ApiException(
                    HttpStatus.SERVICE_UNAVAILABLE,
                    "VM_CONTROL_UNAVAILABLE",
                    "VM 제어 서비스에 연결할 수 없습니다.");
        }
    }

    @FunctionalInterface
    private interface ClientCall<T> {
        T execute();
    }
}

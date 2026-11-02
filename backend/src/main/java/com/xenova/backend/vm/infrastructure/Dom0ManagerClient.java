package com.xenova.backend.vm.infrastructure;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmLaunchSpec;
import com.xenova.backend.vm.domain.VmRegistrationInventory;
import com.xenova.backend.vm.domain.VmRuntime;
import java.io.IOException;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.net.http.HttpClient;
import java.net.http.HttpConnectTimeoutException;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.TimeoutException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.converter.json.AbstractJackson2HttpMessageConverter;
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
    public List<VmRuntime> list() {
        VmRuntime[] response = executeQuery(() -> queryClient.get()
                .uri("/api/v1/vms")
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(VmRuntime[].class));
        return response == null ? List.of() : Arrays.asList(response);
    }

    public List<VmRegistrationInventory> listRegistrations() {
        VmRegistrationInventory[] response = executeQuery(() -> queryClient.get()
                .uri("/api/v1/vm-registrations")
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(VmRegistrationInventory[].class));
        return response == null ? List.of() : Arrays.asList(response);
    }

    // 404 VMNotFound means the domain is absent in Xen, which is how a stopped VM looks to dom0-manager.
    @Override
    public Optional<VmRuntime> find(String vmId) {
        try {
            return Optional.of(requireBody(executeQuery(() -> queryClient.get()
                    .uri("/api/v1/vms/{vmId}", vmId)
                    .retrieve()
                    .onStatus(status -> status.isError(), this::handleError)
                    .body(VmRuntime.class))));
        } catch (ApiException exception) {
            if ("VM_NOT_FOUND".equals(exception.getCode())) {
                return Optional.empty();
            }
            throw exception;
        }
    }

    @Override
    public VmRuntime launch(String vmId, VmLaunchSpec spec) {
        return requireBody(executeCommand(() -> commandClient.post()
                .uri("/api/v1/vms")
                .body(Dom0LaunchRequest.of(vmId, spec))
                .retrieve()
                .onStatus(status -> status.isError(), this::handleError)
                .body(VmRuntime.class)));
    }

    @Override
    public void stop(String vmId) {
        executeCommand(() -> {
            commandClient.post()
                    .uri("/api/v1/vms/{vmId}/stop", vmId)
                    .retrieve()
                    .onStatus(status -> status.isError(), this::handleError)
                    .toBodilessEntity();
            return null;
        });
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
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(properties.connectTimeout())
                .build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(readTimeout);

        RestClient.Builder builder = RestClient.builder()
                .baseUrl(properties.baseUrl())
                .requestFactory(requestFactory)
                // dom0-manager only speaks snake_case JSON. Without this, other Jackson converters on the
                // classpath (e.g. YAML from springdoc) are picked first and the body is not JSON at all.
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
            // Passed on as 507 like dom0-manager: retrying the same request cannot succeed until resources free up.
            case "InsufficientResources" -> new ApiException(
                    HttpStatus.INSUFFICIENT_STORAGE, "INSUFFICIENT_RESOURCES", insufficientResourceMessage(error));
            case "DriverError" -> new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "VM_DRIVER_ERROR", message);
            // The driver or platform does not offer this operation yet (e.g. caller-chosen vm_id before P2 supports it).
            case "NotSupported" -> new ApiException(
                    HttpStatus.NOT_IMPLEMENTED, "VM_CONTROL_NOT_SUPPORTED", "보드가 아직 지원하지 않는 작업입니다.");
            // Temporary registry/storage/Xen trouble or a platform lock held by another operation: retrying later can work,
            // so it uses the same public code as a connection failure.
            case "PlatformUnavailable" -> new ApiException(
                    HttpStatus.SERVICE_UNAVAILABLE,
                    "VM_CONTROL_UNAVAILABLE",
                    "VM 제어 서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도하세요.");
            // dom0-manager ran out of time itself (xl or platform call, including the 90s stop wait).
            case "DriverTimeout" -> new ApiException(
                    HttpStatus.GATEWAY_TIMEOUT, "VM_CONTROL_TIMEOUT", "VM 제어 서비스의 응답 시간이 초과되었습니다.");
            // The service token does not match: a deployment setting, not the user's login, so it is not passed on as 401.
            case "Unauthorized" -> serviceTokenRejected(upstreamStatus);
            default -> {
                if (upstreamStatus == HttpStatus.UNAUTHORIZED.value()) {
                    yield serviceTokenRejected(upstreamStatus);
                }
                log.warn("Unknown dom0-manager error code={}, status={}", error.code(), upstreamStatus);
                yield new ApiException(
                        HttpStatus.BAD_GATEWAY,
                        "VM_CONTROL_BAD_RESPONSE",
                        "VM 제어 서비스가 알 수 없는 오류를 반환했습니다.");
            }
        };
    }

    private static ApiException serviceTokenRejected(int upstreamStatus) {
        log.warn("dom0-manager rejected the service token (status={}); check DOM0_MANAGER_SERVICE_TOKEN "
                + "against XENOVA_SERVICE_TOKEN on the board", upstreamStatus);
        return new ApiException(
                HttpStatus.BAD_GATEWAY,
                "VM_CONTROL_BAD_RESPONSE",
                "VM 제어 서비스와의 연결 설정이 올바르지 않습니다.");
    }

    // dom0-manager's message is English and names internal fields, so the user sees which resource ran out instead.
    private static String insufficientResourceMessage(Dom0ErrorResponse error) {
        String name = error.resource() == null ? null : error.resource().name();
        if (name == null) {
            return "보드의 자원이 부족합니다.";
        }
        return switch (name) {
            case "memory_mib" -> "보드에 남은 메모리가 부족합니다.";
            case "vcpus" -> "보드에 남은 CPU가 부족합니다.";
            case "ipv4" -> "VM에 할당할 수 있는 IP 주소가 없습니다.";
            default -> "보드의 자원이 부족합니다.";
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

    private <T> T requireBody(T body) {
        if (body == null) {
            throw new ApiException(
                    HttpStatus.BAD_GATEWAY,
                    "VM_CONTROL_BAD_RESPONSE",
                    "VM 제어 서비스가 응답 본문을 반환하지 않았습니다.");
        }
        return body;
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

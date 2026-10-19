package com.perseus.backend.vm.infrastructure;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmState;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class Dom0ManagerClientTest {

    private static final Duration RETRY_BACKOFF = Duration.ofMillis(200);

    private HttpServer server;
    private Dom0ManagerClient client;
    private final AtomicReference<String> authorization = new AtomicReference<>();

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        server.start();
        client = clientFor("http://127.0.0.1:" + server.getAddress().getPort());
    }

    private Dom0ManagerClient clientFor(String baseUrl) {
        Dom0ManagerProperties properties = new Dom0ManagerProperties(
                baseUrl,
                "service-secret",
                Duration.ofSeconds(1),
                Duration.ofMillis(300),
                Duration.ofMillis(300),
                RETRY_BACKOFF);
        ObjectMapper objectMapper = new ObjectMapper()
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
        return new Dom0ManagerClient(properties, objectMapper);
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    @Test
    void listReadsSnakeCaseResponseAndSendsServiceToken() {
        server.createContext("/api/v1/vms", exchange -> {
            authorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
            respond(exchange, 200, """
                    [{
                      "vm_id":"550e8400-e29b-41d4-a716-446655440000",
                      "name":"alpine-sensor",
                      "state":"running",
                      "template_id":"alpine",
                      "template_version":"1.2.0",
                      "vcpus":1,
                      "memory_mib":256
                    }]
                    """);
        });

        List<Vm> result = client.list();

        assertThat(result).hasSize(1);
        assertThat(result.getFirst().state()).isEqualTo(VmState.RUNNING);
        assertThat(result.getFirst().memoryMib()).isEqualTo(256);
        assertThat(authorization.get()).isEqualTo("Bearer service-secret");
    }

    @Test
    void mapsControlLayerErrorToPublicApiError() {
        server.createContext("/api/v1/vms/missing", exchange -> respond(exchange, 404, """
                {
                  "code":"VMNotFound",
                  "message":"missing VM",
                  "detail":"xl: domain not found"
                }
                """));

        assertThatThrownBy(() -> client.get("missing"))
                .isInstanceOfSatisfying(ApiException.class, exception -> {
                    assertThat(exception.getStatus().value()).isEqualTo(404);
                    assertThat(exception.getCode()).isEqualTo("VM_NOT_FOUND");
                    assertThat(exception.getMessage()).isEqualTo("missing VM");
                });
    }

    @Test
    void queryTimeoutIsRetriedOnceThenReportedAsTimeout() {
        AtomicInteger requests = new AtomicInteger();
        server.createContext("/api/v1/vms", exchange -> {
            requests.incrementAndGet();
            sleep(Duration.ofSeconds(1));
            respond(exchange, 200, "[]");
        });

        assertThatThrownBy(() -> client.list())
                .isInstanceOfSatisfying(ApiException.class, exception ->
                        assertThat(exception.getCode()).isEqualTo("VM_CONTROL_TIMEOUT"));
        assertThat(requests).hasValue(2);
    }

    @Test
    void commandTimeoutIsNotRetried() {
        AtomicInteger requests = new AtomicInteger();
        server.createContext("/api/v1/vms/vm-1/start", exchange -> {
            requests.incrementAndGet();
            sleep(Duration.ofSeconds(1));
            respond(exchange, 200, "{}");
        });

        assertThatThrownBy(() -> client.start("vm-1"))
                .isInstanceOfSatisfying(ApiException.class, exception -> {
                    assertThat(exception.getStatus().value()).isEqualTo(504);
                    assertThat(exception.getCode()).isEqualTo("VM_CONTROL_TIMEOUT");
                });
        assertThat(requests).hasValue(1);
    }

    @Test
    void commandConnectFailureIsRetriedOnceThenReportedAsUnavailable() throws IOException {
        int closedPort;
        try (ServerSocket socket = new ServerSocket(0)) {
            closedPort = socket.getLocalPort();
        }
        Dom0ManagerClient unreachable = clientFor("http://127.0.0.1:" + closedPort);

        long startedAt = System.nanoTime();
        assertThatThrownBy(() -> unreachable.start("vm-1"))
                .isInstanceOfSatisfying(ApiException.class, exception -> {
                    assertThat(exception.getStatus().value()).isEqualTo(503);
                    assertThat(exception.getCode()).isEqualTo("VM_CONTROL_UNAVAILABLE");
                });
        // The single retry waits for the backoff before the second attempt.
        assertThat(Duration.ofNanos(System.nanoTime() - startedAt)).isGreaterThanOrEqualTo(RETRY_BACKOFF);
    }

    @Test
    void controlLayerErrorIsNotRetried() {
        AtomicInteger requests = new AtomicInteger();
        server.createContext("/api/v1/vms/vm-1/stop", exchange -> {
            requests.incrementAndGet();
            respond(exchange, 409, """
                    {"code":"InvalidState","message":"cannot stop"}
                    """);
        });

        assertThatThrownBy(() -> client.stop("vm-1"))
                .isInstanceOfSatisfying(ApiException.class, exception ->
                        assertThat(exception.getCode()).isEqualTo("INVALID_VM_STATE"));
        assertThat(requests).hasValue(1);
    }

    private void sleep(Duration duration) {
        try {
            Thread.sleep(duration);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        }
    }

    private void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}

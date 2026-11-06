package com.xenova.backend.vm.infrastructure;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.vm.domain.VmLaunchSpec;
import com.xenova.backend.vm.domain.VmControlOperation;
import com.xenova.backend.vm.domain.VmRuntime;
import com.xenova.backend.vm.domain.VmState;
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
import org.springframework.http.HttpStatus;

// Stub dom0-manager responses follow docs/contracts/dom0-manager-openapi.yaml.
class Dom0ManagerClientTest {

    private static final Duration RETRY_BACKOFF = Duration.ofMillis(200);
    private static final String VM_ID = "550e8400-e29b-41d4-a716-446655440000";
    private static final VmLaunchSpec SPEC =
            new VmLaunchSpec("주행 A 개발 VM", "alpine", "1.2.0", 1, 256);

    private final ObjectMapper objectMapper = new ObjectMapper()
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);

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
        return new Dom0ManagerClient(properties, objectMapper);
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    @Test
    void listReadsXenDomainsAndSendsServiceToken() {
        server.createContext("/api/v1/vms", exchange -> {
            authorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
            respond(exchange, 200, """
                    [{"vm_id":"%s","state":"running","vcpus":1,"memory_mib":256}]
                    """.formatted(VM_ID));
        });

        List<VmRuntime> result = client.list();

        assertThat(result).containsExactly(
                new VmRuntime(VM_ID, VmState.RUNNING, 1, 256));
        assertThat(authorization.get()).isEqualTo("Bearer service-secret");
    }

    @Test
    void listRegistrationsReadsTheDedicatedPlatformRegistryResponse() {
        server.createContext(
                "/api/v1/vm-registrations",
                exchange -> respond(exchange, 200, """
                        [{
                          "vm_id":"%s",
                          "vcpus":1,
                          "memory_mib":512,
                          "name":"debian-dev",
                          "guest_kind":"linux",
                          "template_id":"debian-13-arm64",
                          "template_version":"1.0.2",
                          "assigned_ipv4":"192.168.77.30"
                        }]
                        """.formatted(VM_ID)));

        var vm = client.listRegistrations().getFirst();

        assertThat(vm.name()).isEqualTo("debian-dev");
        assertThat(vm.guestKind())
                .isEqualTo(com.xenova.backend.template.domain.GuestKind.LINUX);
        assertThat(vm.templateId()).isEqualTo("debian-13-arm64");
        assertThat(vm.templateVersion()).isEqualTo("1.0.2");
        assertThat(vm.assignedIpv4()).isEqualTo("192.168.77.30");
    }

    @Test
    void runtimeLogsForwardOnlyQueryAndServerServiceToken() {
        AtomicReference<String> query = new AtomicReference<>();

        server.createContext("/api/v1/logs", exchange -> {
            authorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
            query.set(exchange.getRequestURI().getQuery());
            respond(
                    exchange,
                    200,
                    """
                    {
                      "epoch":"one",
                      "items":[],
                      "next_cursor":42,
                      "has_more":false,
                      "history_truncated":false
                    }
                    """);
        });

        JsonNode page = client.runtimeLogs(
                java.util.Map.of(
                        "after", 41,
                        "component", "api.service"),
                false);

        assertThat(page.path("next_cursor").asInt()).isEqualTo(42);
        assertThat(query.get()).contains(
                "after=41",
                "component=api.service");
        assertThat(authorization.get()).isEqualTo("Bearer service-secret");
    }

    @Test
    void findTreatsVmNotFoundAsAbsentDomain() {
        server.createContext(
                "/api/v1/vms/" + VM_ID,
                exchange -> respond(exchange, 404, """
                        {
                          "code":"VMNotFound",
                          "message":"no domain",
                          "detail":"xl: domain not found"
                        }
                        """));

        assertThat(client.find(VM_ID)).isEmpty();
    }

    @Test
    void launchPostsTheOpenApiBodyWithSpringName() throws IOException {
        AtomicReference<String> body = new AtomicReference<>();
        AtomicReference<String> contentType = new AtomicReference<>();
        AtomicReference<String> upgrade = new AtomicReference<>();

        server.createContext("/api/v1/vms", exchange -> {
            contentType.set(exchange.getRequestHeaders().getFirst("Content-Type"));
            upgrade.set(exchange.getRequestHeaders().getFirst("Upgrade"));
            body.set(new String(
                    exchange.getRequestBody().readAllBytes(),
                    StandardCharsets.UTF_8));
            respond(exchange, 201, """
                    {
                      "vm_id":"%s",
                      "state":"running",
                      "vcpus":1,
                      "memory_mib":256
                    }
                    """.formatted(VM_ID));
        });

        VmRuntime runtime = client.launch(VM_ID, SPEC);

        assertThat(runtime.state()).isEqualTo(VmState.RUNNING);
        assertThat(contentType.get()).startsWith("application/json");
        assertThat(upgrade.get()).isNull();

        JsonNode sent = objectMapper.readTree(body.get());

        assertThat(sent.fieldNames()).toIterable()
                .containsExactlyInAnyOrder(
                        "vm_id",
                        "name",
                        "template_id",
                        "template_version",
                        "vcpus",
                        "memory_mib");
        assertThat(sent.path("name").asText()).isEqualTo(SPEC.name());
        assertThat(sent.path("vm_id").asText()).isEqualTo(VM_ID);
        assertThat(sent.path("template_id").asText())
                .isEqualTo(SPEC.templateId());
        assertThat(sent.path("template_version").asText())
                .isEqualTo(SPEC.templateVersion());
    }

    @Test
    void stopAcceptsNoContent() {
        AtomicInteger requests = new AtomicInteger();

        server.createContext("/api/v1/vms/" + VM_ID + "/stop", exchange -> {
            requests.incrementAndGet();
            exchange.sendResponseHeaders(204, -1);
            exchange.close();
        });

        client.stop(VM_ID);

        assertThat(requests).hasValue(1);
    }

    @Test
    void rebootPostsTheIdempotencyKeyAndReadsThePendingOperation() {
        AtomicInteger requests = new AtomicInteger();
        AtomicReference<String> method = new AtomicReference<>();
        AtomicReference<String> requestKey = new AtomicReference<>();

        server.createContext("/api/v1/vms/" + VM_ID + "/reboot", exchange -> {
            requests.incrementAndGet();
            method.set(exchange.getRequestMethod());
            requestKey.set(exchange.getRequestHeaders().getFirst("Idempotency-Key"));
            respond(exchange, 202, controlOperation("pending"));
        });

        VmControlOperation operation = client.requestReboot(VM_ID, "reboot-1");

        assertThat(requests).hasValue(1);
        assertThat(method.get()).isEqualTo("POST");
        assertThat(requestKey.get()).isEqualTo("reboot-1");
        assertThat(operation.status()).isEqualTo("pending");
        assertThat(operation.requestKey()).isEqualTo("reboot-1");
    }

    @Test
    void controlOperationPassesTheRequestKeyAsAQueryParameter() {
        AtomicReference<String> query = new AtomicReference<>();
        server.createContext("/api/v1/vms/" + VM_ID + "/control-operations", exchange -> {
            query.set(exchange.getRequestURI().getQuery());
            respond(exchange, 200, controlOperation("completed"));
        });

        VmControlOperation operation = client.findControlOperation(VM_ID, "reboot-1");

        assertThat(query.get()).isEqualTo("request_key=reboot-1");
        assertThat(operation.status()).isEqualTo("completed");
    }

    // Visible ASCII keys may hold '+', '{' or '&': they must reach dom0-manager unchanged, not as a space,
    // a broken URI template (500) or an extra parameter.
    @Test
    void controlOperationEncodesReservedCharactersInTheRequestKey() {
        AtomicReference<String> query = new AtomicReference<>();
        server.createContext("/api/v1/vms/" + VM_ID + "/control-operations", exchange -> {
            query.set(exchange.getRequestURI().getRawQuery());
            respond(exchange, 200, controlOperation("completed"));
        });

        client.findControlOperation(VM_ID, "a+b{c}&d=e");

        assertThat(query.get()).isEqualTo("request_key=a%2Bb%7Bc%7D%26d%3De");
    }

    @Test
    void runtimeLogsEncodesFilterValues() {
        AtomicReference<String> query = new AtomicReference<>();
        server.createContext("/api/v1/logs", exchange -> {
            query.set(exchange.getRequestURI().getRawQuery());
            respond(exchange, 200, """
                    {"epoch":"one","items":[],"next_cursor":0,"has_more":false,"history_truncated":false}
                    """);
        });

        client.runtimeLogs(java.util.Map.of("component", "a+b{x}"), false);

        assertThat(query.get()).isEqualTo("component=a%2Bb%7Bx%7D");
    }

    @Test
    void insufficientResourcesIsMappedToPublicError() {
        server.createContext(
                "/api/v1/vms",
                exchange -> respond(exchange, 507, """
                        {
                          "code":"InsufficientResources",
                          "message":"메모리가 부족합니다.",
                          "resource":{
                            "name":"memory_mib",
                            "requested":1024,
                            "available":512
                          }
                        }
                        """));

        assertThatThrownBy(() -> client.launch(VM_ID, SPEC))
                .isInstanceOfSatisfying(
                        ApiException.class,
                        exception -> {
                            assertThat(exception.getStatus())
                                    .isEqualTo(HttpStatus.INSUFFICIENT_STORAGE);
                            assertThat(exception.getCode())
                                    .isEqualTo("INSUFFICIENT_RESOURCES");
                            assertThat(exception.getMessage())
                                    .isEqualTo("보드에 남은 메모리가 부족합니다.");
                        });
    }

    @Test
    void exhaustedIpPoolIsReportedAsInsufficientResources() {
        server.createContext(
                "/api/v1/vms",
                exchange -> respond(exchange, 507, """
                        {
                          "code":"InsufficientResources",
                          "message":"insufficient ipv4: requested 1, available 0",
                          "resource":{
                            "name":"ipv4",
                            "requested":1,
                            "available":0
                          }
                        }
                        """));

        assertThatThrownBy(() -> client.launch(VM_ID, SPEC))
                .isInstanceOfSatisfying(
                        ApiException.class,
                        exception -> {
                            assertThat(exception.getStatus())
                                    .isEqualTo(HttpStatus.INSUFFICIENT_STORAGE);
                            assertThat(exception.getMessage())
                                    .isEqualTo("VM에 할당할 수 있는 IP 주소가 없습니다.");
                        });
    }

    @Test
    void notSupportedIsMappedTo501() {
        assertLaunchFailsWith(
                501,
                """
                {
                  "code":"NotSupported",
                  "message":"caller-chosen vm_id is not supported by the platform yet"
                }
                """,
                HttpStatus.NOT_IMPLEMENTED,
                "VM_CONTROL_NOT_SUPPORTED");
    }

    @Test
    void platformUnavailableIsMappedTo503() {
        assertLaunchFailsWith(
                503,
                """
                {
                  "code":"PlatformUnavailable",
                  "message":"platform operation in progress"
                }
                """,
                HttpStatus.SERVICE_UNAVAILABLE,
                "VM_CONTROL_UNAVAILABLE");
    }

    @Test
    void driverTimeoutIsMappedTo504() {
        assertLaunchFailsWith(
                504,
                """
                {
                  "code":"DriverTimeout",
                  "message":"xl create timed out"
                }
                """,
                HttpStatus.GATEWAY_TIMEOUT,
                "VM_CONTROL_TIMEOUT");
    }

    @Test
    void rejectedServiceTokenIsNotPassedOnAsAUserAuthError() {
        assertLaunchFailsWith(
                401,
                """
                {
                  "code":"Unauthorized",
                  "message":"invalid service token"
                }
                """,
                HttpStatus.BAD_GATEWAY,
                "VM_CONTROL_BAD_RESPONSE");
    }

    @Test
    void a401WithAnUnexpectedBodyIsStillTreatedAsATokenProblem() {
        assertLaunchFailsWith(
                401,
                """
                {
                  "code":"SomethingElse",
                  "message":"nope"
                }
                """,
                HttpStatus.BAD_GATEWAY,
                "VM_CONTROL_BAD_RESPONSE");
    }

    @Test
    void driverErrorsAreNotRetried() {
        AtomicInteger requests = new AtomicInteger();

        server.createContext("/api/v1/vms", exchange -> {
            requests.incrementAndGet();
            respond(exchange, 503, """
                    {
                      "code":"PlatformUnavailable",
                      "message":"busy"
                    }
                    """);
        });

        assertThatThrownBy(() -> client.launch(VM_ID, SPEC))
                .isInstanceOf(ApiException.class);
        assertThat(requests).hasValue(1);
    }

    private void assertLaunchFailsWith(
            int dom0Status,
            String body,
            HttpStatus status,
            String code) {

        server.createContext(
                "/api/v1/vms",
                exchange -> respond(exchange, dom0Status, body));

        assertThatThrownBy(() -> client.launch(VM_ID, SPEC))
                .isInstanceOfSatisfying(
                        ApiException.class,
                        exception -> {
                            assertThat(exception.getStatus()).isEqualTo(status);
                            assertThat(exception.getCode()).isEqualTo(code);
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

        assertThatThrownBy(client::list)
                .isInstanceOfSatisfying(
                        ApiException.class,
                        exception -> assertThat(exception.getCode())
                                .isEqualTo("VM_CONTROL_TIMEOUT"));
        assertThat(requests).hasValue(2);
    }

    @Test
    void commandTimeoutIsNotRetried() {
        AtomicInteger requests = new AtomicInteger();

        server.createContext("/api/v1/vms/" + VM_ID + "/stop", exchange -> {
            requests.incrementAndGet();
            sleep(Duration.ofSeconds(1));
            exchange.sendResponseHeaders(204, -1);
            exchange.close();
        });

        assertThatThrownBy(() -> client.stop(VM_ID))
                .isInstanceOfSatisfying(
                        ApiException.class,
                        exception -> {
                            assertThat(exception.getStatus().value()).isEqualTo(504);
                            assertThat(exception.getCode())
                                    .isEqualTo("VM_CONTROL_TIMEOUT");
                        });
        assertThat(requests).hasValue(1);
    }

    @Test
    void commandConnectFailureIsRetriedOnceThenReportedAsUnavailable()
            throws IOException {

        int closedPort;
        try (ServerSocket socket = new ServerSocket(0)) {
            closedPort = socket.getLocalPort();
        }

        Dom0ManagerClient unreachable =
                clientFor("http://127.0.0.1:" + closedPort);

        long startedAt = System.nanoTime();

        assertThatThrownBy(() -> unreachable.launch(VM_ID, SPEC))
                .isInstanceOfSatisfying(
                        ApiException.class,
                        exception -> {
                            assertThat(exception.getStatus().value()).isEqualTo(503);
                            assertThat(exception.getCode())
                                    .isEqualTo("VM_CONTROL_UNAVAILABLE");
                        });

        assertThat(Duration.ofNanos(System.nanoTime() - startedAt))
                .isGreaterThanOrEqualTo(RETRY_BACKOFF);
    }

    @Test
    void vmExistsIsNotRetried() {
        AtomicInteger requests = new AtomicInteger();

        server.createContext("/api/v1/vms", exchange -> {
            requests.incrementAndGet();
            respond(exchange, 409, """
                    {
                      "code":"VMExists",
                      "message":"vm_id already exists"
                    }
                    """);
        });

        assertThatThrownBy(() -> client.launch(VM_ID, SPEC))
                .isInstanceOfSatisfying(
                        ApiException.class,
                        exception -> assertThat(exception.getCode())
                                .isEqualTo("VM_ALREADY_EXISTS"));
        assertThat(requests).hasValue(1);
    }

    private static String controlOperation(String status) {
        return """
                {
                  "vm_id":"%s",
                  "action":"reboot",
                  "request_key":"reboot-1",
                  "operation_id":"650e8400-e29b-41d4-a716-446655440000",
                  "status":"%s",
                  "accepted":true,
                  "phase":"%s",
                  "warnings":[]
                }
                """.formatted(VM_ID, status, status);
    }

    private void sleep(Duration duration) {
        try {
            Thread.sleep(duration);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        }
    }

    private void respond(
            HttpExchange exchange,
            int status,
            String body) throws IOException {

        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set(
                "Content-Type",
                "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}

package com.xenova.backend.display;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
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
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class DisplayIntegrationTest {
    static final HttpServer upstream;
    static final AtomicInteger calls = new AtomicInteger();
    static final AtomicInteger status = new AtomicInteger(200);
    static final AtomicReference<String> credential = new AtomicReference<>();
    static final String STATE = """
            {"schema_version":1,"type":"display.state","source_epoch":"feed-1","sequence":5,
             "vms":[{"vm_id":"vm1","role":"primary_drive"},{"vm_id":"vm2","role":"backup_drive"},
                    {"vm_id":"vm3","role":"interaction"}],"drive":{"mode":"MANUAL","active_vm_id":"vm1"}}
            """;
    static {
        try {
            upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            upstream.createContext("/api/v1/display/state", exchange -> {
                calls.incrementAndGet();
                credential.set(exchange.getRequestHeaders().getFirst("Authorization"));
                byte[] bytes = STATE.getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(status.get(), bytes.length);
                try (var output = exchange.getResponseBody()) { output.write(bytes); }
            });
            upstream.start();
        } catch (Exception error) { throw new ExceptionInInitializerError(error); }
    }

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("xenova.display.enabled", () -> true);
        registry.add("xenova.display.base-url", () -> "http://127.0.0.1:" + upstream.getAddress().getPort());
        registry.add("xenova.display.service-token", () -> "private-feed-token");
    }

    @LocalServerPort int port;
    @Autowired TestRestTemplate rest;
    @Autowired ObjectMapper json;

    @BeforeEach void reset() { calls.set(0); status.set(200); credential.set(null); }
    @AfterAll static void stop() { upstream.stop(0); }

    @Test void restRequiresUserJwtAndHidesServiceCredential() throws Exception {
        assertThat(rest.getForEntity("/api/v1/display/state", String.class).getStatusCode().value()).isEqualTo(401);
        assertThat(calls.get()).isZero();
        HttpHeaders headers = new HttpHeaders(); headers.setBearerAuth(login());
        var response = rest.exchange("/api/v1/display/state", org.springframework.http.HttpMethod.GET,
                new HttpEntity<>(headers), String.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(json.readTree(response.getBody()).path("vms").get(1).path("role").asText()).isEqualTo("backup_drive");
        assertThat(credential.get()).isEqualTo("Bearer private-feed-token");
        assertThat(response.getBody()).doesNotContain("private-feed-token");
    }

    @Test void websocketAuthenticatesStreamsAndRejectsControlInput() throws Exception {
        try (Client client = connect()) {
            client.send("{\"type\":\"auth\",\"access_token\":\"" + login() + "\"}");
            assertThat(json.readTree(client.next()).path("type").asText()).isEqualTo("authenticated");
            var snapshot = json.readTree(client.next());
            assertThat(snapshot.path("type").asText()).isEqualTo("display.state");
            assertThat(snapshot.path("state").path("drive").path("active_vm_id").asText()).isEqualTo("vm1");
            client.send("{\"type\":\"input\",\"keys\":[\"w\"]}");
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(1008);
        }
    }

    @Test void invalidJwtNeverFetchesUpstream() throws Exception {
        try (Client client = connect()) {
            client.send("{\"type\":\"auth\",\"access_token\":\"invalid\"}");
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
            assertThat(calls.get()).isZero();
        }
    }

    @Test void kioskCookieIsNotAcceptedByBackendReadOrAdminApis() {
        HttpHeaders kiosk = new HttpHeaders();
        kiosk.add(HttpHeaders.COOKIE, "xenova_display=local-kiosk-session");
        assertThat(rest.exchange("/api/v1/display/state", HttpMethod.GET,
                new HttpEntity<>(kiosk), String.class).getStatusCode().value()).isEqualTo(401);
        assertThat(rest.exchange("/api/v1/drive/state", HttpMethod.GET,
                new HttpEntity<>(kiosk), String.class).getStatusCode().value()).isEqualTo(401);
        assertThat(rest.exchange("/api/v1/vms", HttpMethod.POST,
                new HttpEntity<>("{}", kiosk), String.class).getStatusCode().value()).isEqualTo(401);
        assertThat(calls.get()).isZero();
    }

    @Test void upstreamFailureIsExplicitAndNextSnapshotRecovers() throws Exception {
        status.set(503);
        try (Client client = connect()) {
            client.send("{\"type\":\"auth\",\"access_token\":\"" + login() + "\"}");
            client.next();
            assertThat(json.readTree(client.next()).path("type").asText()).isEqualTo("display.unavailable");
            status.set(200);
            assertThat(json.readTree(client.next()).path("type").asText()).isEqualTo("display.state");
        }
    }

    String login() throws Exception {
        HttpHeaders headers = new HttpHeaders(); headers.setContentType(MediaType.APPLICATION_JSON);
        String body = "{\"username\":\"display-test-user\",\"nickname\":\"디스플레이 통합 테스트\",\"password\":\"password123!\"}";
        rest.postForEntity("/api/v1/auth/signup", new HttpEntity<>(body, headers), String.class);
        String reply = rest.postForEntity("/api/v1/auth/login", new HttpEntity<>(body, headers), String.class).getBody();
        return json.readTree(reply).path("access_token").asText();
    }

    Client connect() throws Exception {
        Client c = new Client();
        c.session = new StandardWebSocketClient().execute(c, "ws://127.0.0.1:" + port + "/ws/v1/display/state")
                .get(5, TimeUnit.SECONDS);
        return c;
    }

    static class Client extends TextWebSocketHandler implements AutoCloseable {
        WebSocketSession session;
        final BlockingQueue<String> messages = new LinkedBlockingQueue<>();
        final CompletableFuture<CloseStatus> closed = new CompletableFuture<>();
        @Override protected void handleTextMessage(WebSocketSession session, TextMessage message) { messages.add(message.getPayload()); }
        @Override public void afterConnectionClosed(WebSocketSession session, CloseStatus status) { closed.complete(status); }
        void send(String text) throws Exception { session.sendMessage(new TextMessage(text)); }
        String next() throws Exception { String s = messages.poll(6, TimeUnit.SECONDS); assertThat(s).isNotNull(); return s; }
        @Override public void close() throws Exception { if (session.isOpen()) session.close(); }
    }
}

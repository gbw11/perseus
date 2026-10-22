package com.xenova.backend.console;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.auth.application.AccessTokenVerifier.InvalidAccessTokenException;
import com.xenova.backend.auth.application.AccessTokenVerifier.VerifiedToken;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.application.VmService;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmState;
import com.xenova.backend.vm.infrastructure.Dom0ManagerProperties;
import java.io.IOException;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.handler.AbstractWebSocketHandler;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Import(ConsoleWebSocketIntegrationTest.UpstreamConfig.class)
class ConsoleWebSocketIntegrationTest {
    static final String VM_ID = "11111111-1111-4111-8111-111111111111";
    @LocalServerPort int port;
    @Autowired TestRestTemplate rest;
    @Autowired ObjectMapper json;
    @Autowired Echo echo;
    @MockitoBean VmService vms;
    @MockitoSpyBean Dom0ManagerProperties properties;
    @MockitoSpyBean AccessTokenVerifier verifier;

    @BeforeEach
    void setup() {
        // The stand-in dom0 uses the auth-exempt test path; its token is not a user JWT.
        doReturn("http://127.0.0.1:" + port + "/api/v1/auth/test-dom0").when(properties).baseUrl();
        when(vms.get(VM_ID)).thenReturn(vm(VmState.RUNNING));
        echo.credentials.clear();
    }

    @Test
    void authenticatesRealLoginRelaysRawBytesAndKeepsServiceTokenOnServer() throws Exception {
        try (Client client = connect()) {
            client.auth(login());
            assertThat(json.readTree((String) client.next()).path("type").asText()).isEqualTo("connected");
            byte[] input = new byte[] {0x03, 0x1b, '[', 'A', '\r', (byte) 0xe3, (byte) 0xff};
            client.session.sendMessage(new BinaryMessage(input));
            assertThat((byte[]) client.next()).isEqualTo(input);
            assertThat(echo.credentials).containsExactly("Bearer test-service-token");
        }
    }

    @Test
    void noVmLookupOrUpstreamConnectionBeforeUserAuthentication() throws Exception {
        try (Client client = connect()) {
            client.auth("invalid-jwt");
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
            verify(vms, never()).get(anyString());
            assertThat(echo.credentials).isEmpty();
        }
    }

    @Test
    void inputBeforeAuthenticationIsRejected() throws Exception {
        try (Client client = connect()) {
            client.session.sendMessage(new BinaryMessage(new byte[] {13}));
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
            assertThat(echo.credentials).isEmpty();
        }
    }

    @Test
    void explicitDisconnectWaitsForUpstreamCleanupAcknowledgement() throws Exception {
        try (Client client = connect()) {
            client.auth(login());
            client.next();
            client.session.sendMessage(new TextMessage("{\"type\":\"disconnect\"}"));
            assertThat(echo.detachReceived.poll(5, TimeUnit.SECONDS)).isNotNull();
            assertThat(client.closed.isDone()).isFalse();
            echo.detachReceivedSession.close(CloseStatus.NORMAL);
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(1000);
        }
    }

    @Test
    void registeredVmAndStateAreCheckedBeforeUpstreamConnection() throws Exception {
        when(vms.get(VM_ID)).thenReturn(vm(VmState.STOPPED));
        try (Client client = connect()) {
            client.auth(login());
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4409);
        }
        when(vms.get(VM_ID)).thenThrow(new ApiException(HttpStatus.NOT_FOUND, "VM_NOT_FOUND", "missing"));
        try (Client client = connect()) {
            client.auth(login());
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4404);
        }
        assertThat(echo.credentials).isEmpty();
    }

    @Test
    void upstreamDuplicateCloseCodeIsForwarded() throws Exception {
        try (Client client = connect()) {
            client.auth(login());
            client.next();
            client.session.sendMessage(new BinaryMessage("UPSTREAM_BUSY".getBytes()));
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4409);
        }
    }

    @Test
    void expiredLoginClosesEvenWithoutTerminalInput() throws Exception {
        doReturn(new VerifiedToken("short-lived", Instant.now().plusSeconds(2))).when(verifier).verify("short-token");
        try (Client client = connect()) {
            client.auth("short-token");
            client.next();
            assertThat(client.closed.get(6, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
        }
    }

    @Test
    void revokedTokenCannotSendMoreInput() throws Exception {
        String token = login();
        try (Client client = connect()) {
            client.auth(token);
            client.next();
            doThrow(new InvalidAccessTokenException("revoked")).when(verifier).verify(token);
            client.session.sendMessage(new BinaryMessage(new byte[] {13}));
            assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
        }
    }

    private Vm vm(VmState state) {
        return new Vm(VM_ID, "debian-dev", GuestKind.LINUX, state, "debian-13-arm64", "1.0.0", 1, 512);
    }

    private String login() throws Exception {
        String body = "{\"username\":\"console-test-user\",\"password\":\"password123!\"}";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        rest.postForEntity("/api/v1/auth/signup", new HttpEntity<>(body, headers), String.class);
        String reply = rest.postForEntity("/api/v1/auth/login", new HttpEntity<>(body, headers), String.class).getBody();
        return json.readTree(reply).path("access_token").asText();
    }

    private Client connect() throws Exception {
        Client client = new Client();
        client.session = new StandardWebSocketClient().execute(client,
                "ws://127.0.0.1:" + port + "/ws/v1/vms/" + VM_ID + "/console").get(5, TimeUnit.SECONDS);
        return client;
    }

    @TestConfiguration
    static class UpstreamConfig {
        @Bean Echo echo() { return new Echo(); }
        @Bean WebSocketConfigurer fakeDom0(Echo echo) {
            return registry -> registry.addHandler(echo, "/api/v1/auth/test-dom0/api/v1/vms/*/console")
                    .setAllowedOriginPatterns("*");
        }
    }

    static class Echo extends AbstractWebSocketHandler {
        final List<String> credentials = new CopyOnWriteArrayList<>();
        final BlockingQueue<Boolean> detachReceived = new LinkedBlockingQueue<>();
        volatile WebSocketSession detachReceivedSession;
        @Override protected void handleTextMessage(WebSocketSession session, TextMessage message) {
            if (message.getPayload().equals("{\"type\":\"disconnect\"}")) {
                detachReceivedSession = session;
                detachReceived.add(true);
            }
        }
        @Override public void afterConnectionEstablished(WebSocketSession session) {
            credentials.add(session.getHandshakeHeaders().getFirst("Authorization"));
        }
        @Override protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) throws IOException {
            byte[] bytes = new byte[message.getPayloadLength()];
            message.getPayload().get(bytes);
            if (new String(bytes).equals("UPSTREAM_BUSY")) session.close(new CloseStatus(4409, "busy"));
            else session.sendMessage(new BinaryMessage(bytes));
        }
    }

    static class Client extends AbstractWebSocketHandler implements AutoCloseable {
        final BlockingQueue<Object> messages = new LinkedBlockingQueue<>();
        final CompletableFuture<CloseStatus> closed = new CompletableFuture<>();
        WebSocketSession session;
        @Override protected void handleTextMessage(WebSocketSession session, TextMessage message) { messages.add(message.getPayload()); }
        @Override protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) {
            byte[] bytes = new byte[message.getPayloadLength()];
            message.getPayload().get(bytes);
            messages.add(bytes);
        }
        @Override public void afterConnectionClosed(WebSocketSession session, CloseStatus status) { closed.complete(status); }
        void auth(String token) throws IOException { session.sendMessage(new TextMessage("{\"type\":\"auth\",\"access_token\":\"" + token + "\"}")); }
        Object next() throws Exception {
            Object message = messages.poll(5, TimeUnit.SECONDS);
            assertThat(message).isNotNull();
            return message;
        }
        @Override public void close() throws IOException { if (session.isOpen()) session.close(); }
    }
}

package com.xenova.backend.event.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.xenova.backend.event.domain.RecoverySyncState;
import com.xenova.backend.event.domain.RecoverySyncStateRepository;
import com.xenova.backend.event.domain.VmEventRepository;
import com.xenova.backend.event.domain.VmRecoveryHealth;
import com.xenova.backend.event.domain.VmRecoveryHealthRepository;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.BooleanSupplier;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

@SpringBootTest
class RecoveryEventBackfillIntegrationTest {

    private static final Instant RECEIVED_AT = Instant.parse("2026-10-28T00:00:02Z");
    private static final String VM_ID = "00000000-0000-0000-0000-000000000002";
    private static final String RECOVERY_ID = "00000000-0000-0000-0000-000000000003";
    private static final String ATTEMPT_ID = "00000000-0000-0000-0000-000000000004";
    private static final String CURSOR_ID = "00000000-0000-0000-0001-000000000500";
    private static final String DUPLICATE_ID = "00000000-0000-0000-0001-000000000501";
    private static final String NEW_EVENT_ID = "00000000-0000-0000-0001-000000000502";

    private static final int PORT = freePort();
    private static final ConcurrentLinkedQueue<Response> RESPONSES = new ConcurrentLinkedQueue<>();
    private static final CopyOnWriteArrayList<String> QUERIES = new CopyOnWriteArrayList<>();
    private static final HttpServer SERVER = startServer(PORT);

    @Autowired
    private RecoveryEventBackfillService backfillService;

    @Autowired
    private RecoveryEventMessageHandler handler;

    @Autowired
    private RecoverySyncStateRepository syncStateRepository;

    @Autowired
    private VmEventRepository eventRepository;

    @Autowired
    private VmRecoveryHealthRepository healthRepository;

    @DynamicPropertySource
    static void dom0ManagerProperties(DynamicPropertyRegistry registry) {
        registry.add("xenova.dom0-manager.base-url", () -> "http://127.0.0.1:" + PORT);
        registry.add("xenova.dom0-manager.retry-backoff", () -> "10ms");
    }

    @BeforeEach
    void reset() {
        healthRepository.deleteAll();
        eventRepository.deleteAll();
        syncStateRepository.deleteAll();
        RESPONSES.clear();
        QUERIES.clear();
    }

    @AfterAll
    static void stopServer() {
        SERVER.stop(0);
    }

    @Test
    void retries503FromTheSameCursorAndPersistsOnlyMissingEvents() throws Exception {
        assertThat(handler.handle(startedEvent(DUPLICATE_ID).getBytes(StandardCharsets.UTF_8), RECEIVED_AT))
                .isEqualTo(RecoveryEventMessageHandler.Result.ACCEPTED);
        RecoverySyncState cursor = new RecoverySyncState(RecoverySyncState.RECOVERY_EVENTS, RECEIVED_AT);
        cursor.advance(CURSOR_ID, RECEIVED_AT);
        syncStateRepository.saveAndFlush(cursor);

        RESPONSES.add(new Response(503, ""));
        RESPONSES.add(new Response(200, page(
                List.of(startedEvent(DUPLICATE_ID), completedEvent(NEW_EVENT_ID)), NEW_EVENT_ID)));
        RESPONSES.add(new Response(200, emptyPage(NEW_EVENT_ID)));

        backfillService.requestBackfill();

        await(() -> syncStateRepository.findById(RecoverySyncState.RECOVERY_EVENTS)
                .map(RecoverySyncState::getAfterEventId)
                .filter(NEW_EVENT_ID::equals)
                .isPresent());
        await(() -> QUERIES.size() >= 3);

        assertThat(QUERIES).hasSize(3);
        assertThat(QUERIES.get(0)).contains("after_event_id=" + CURSOR_ID);
        assertThat(QUERIES.get(1)).contains("after_event_id=" + CURSOR_ID);
        assertThat(QUERIES.get(2)).contains("after_event_id=" + NEW_EVENT_ID);
        assertThat(eventRepository.countByEventId(DUPLICATE_ID)).isEqualTo(1);
        assertThat(eventRepository.countByEventId(NEW_EVENT_ID)).isEqualTo(1);
        assertThat(eventRepository.count()).isEqualTo(2);

        VmRecoveryHealth health = healthRepository.findById(VM_ID).orElseThrow();
        assertThat(health.getLastEventId()).isEqualTo(NEW_EVENT_ID);
        assertThat(health.getStatus()).isEqualTo("COMPLETED");
        assertThat(health.getRecoveriesTotal()).isEqualTo(1);
        assertThat(health.getLastRecoveryDurationMs()).isEqualTo(1_250L);
    }

    private static void await(BooleanSupplier condition) throws InterruptedException {
        Instant deadline = Instant.now().plus(Duration.ofSeconds(10));
        while (!condition.getAsBoolean() && Instant.now().isBefore(deadline)) {
            Thread.sleep(20);
        }
        assertThat(condition.getAsBoolean()).isTrue();
    }

    private static String page(List<String> events, String nextCursor) {
        return "{\"events\":[" + String.join(",", events) + "],"
                + "\"next_after_event_id\":\"" + nextCursor + "\","
                + "\"oldest_event_id\":\"" + CURSOR_ID + "\",\"gap\":false}";
    }

    private static String emptyPage(String oldestEventId) {
        return "{\"events\":[],\"next_after_event_id\":null,"
                + "\"oldest_event_id\":\"" + oldestEventId + "\",\"gap\":false}";
    }

    private static String startedEvent(String eventId) {
        return commonEvent(eventId, "recovery.started", "");
    }

    private static String completedEvent(String eventId) {
        return commonEvent(eventId, "recovery.completed",
                ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1250,"
                        + "\"execution_id\":\"exec-new\"");
    }

    private static String commonEvent(String eventId, String type, String terminalFields) {
        return ("""
                {"event_id":"%s","type":"%s","vm_id":"%s","recovery_id":"%s",
                 "recovery_attempt_id":"%s","attempt":1,"reason":"HEARTBEAT_LOST",
                 "mode":"graceful-restart","detected_at":"2026-10-28T00:00:00Z",
                 "occurred_at":"2026-10-28T00:00:01Z"%s}
                """).formatted(eventId, type, VM_ID, RECOVERY_ID, ATTEMPT_ID, terminalFields).strip();
    }

    private static int freePort() {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        } catch (IOException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static HttpServer startServer(int port) {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
            server.createContext("/api/v1/recovery/events", RecoveryEventBackfillIntegrationTest::respond);
            server.start();
            return server;
        } catch (IOException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static void respond(HttpExchange exchange) throws IOException {
        QUERIES.add(exchange.getRequestURI().getRawQuery());
        Response response = RESPONSES.poll();
        if (response == null) {
            response = new Response(200, emptyPage(CURSOR_ID));
        }
        byte[] body = response.body().getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status(), body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private record Response(int status, String body) {
    }
}

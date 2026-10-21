package com.xenova.backend.drive.application;

import java.time.Instant;
import java.util.List;

// GET /api/v1/messaging/stats: per topic and message type, how many arrived and how many in the last second.
// rejected counts messages dropped by envelope checks (contract §2).
public record MessagingStats(Instant observedAt, long rejected, List<Entry> items) {

    public record Entry(String topic, String type, long total, int lastSecond, long lastReceivedAgoMs) {
    }
}

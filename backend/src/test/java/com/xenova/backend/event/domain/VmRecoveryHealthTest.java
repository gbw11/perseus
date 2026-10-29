package com.xenova.backend.event.domain;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import org.junit.jupiter.api.Test;

class VmRecoveryHealthTest {

    @Test
    void countsOnlyFinalFailuresAndUsesCompletedDuration() {
        Instant now = Instant.parse("2026-10-28T00:00:00Z");
        VmRecoveryHealth health = new VmRecoveryHealth("vm-1", now);

        health.update("event-1", "recovery-1", "attempt-1", "FAILED", 1,
                false, false, null, now, now);
        health.update("event-2", "recovery-1", "attempt-2", "FAILED", 2,
                false, true, null, now, now);
        health.update("event-3", "recovery-2", "attempt-3", "COMPLETED", 1,
                true, false, 1_250L, now, now);

        assertThat(health.getFinalFailuresTotal()).isEqualTo(1);
        assertThat(health.getRecoveriesTotal()).isEqualTo(1);
        assertThat(health.getLastRecoveryDurationMs()).isEqualTo(1_250L);
    }
}

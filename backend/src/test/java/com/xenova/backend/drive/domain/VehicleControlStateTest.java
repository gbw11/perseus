package com.xenova.backend.drive.domain;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import org.junit.jupiter.api.Test;

class VehicleControlStateTest {

    @Test
    void keepsControlAndVehicleFreshnessSourcesSeparate() {
        Instant controlAt = Instant.parse("2026-10-28T00:00:00Z");
        Instant statusAt = controlAt.plusMillis(50);
        VehicleControlState state = new VehicleControlState("DEFAULT", controlAt);

        state.updateControl("AUTO", "AUTO_READY", "vm-primary", "autonomous", 7L,
                "boot-control", "gateway-1", 11L, controlAt, controlAt);
        state.updateVehicleStatus(7L, "boot-status", "esp32-1", 21L, statusAt);

        assertThat(state.getActiveVmId()).isEqualTo("vm-primary");
        assertThat(state.getGeneration()).isEqualTo(7L);
        assertThat(state.getAppliedGeneration()).isEqualTo(7L);
        assertThat(state.getControlSeq()).isEqualTo(11L);
        assertThat(state.getStatusSeq()).isEqualTo(21L);
        assertThat(state.getControlReceivedAt()).isEqualTo(controlAt);
        assertThat(state.getStatusReceivedAt()).isEqualTo(statusAt);
        assertThat(state.getUpdatedAt()).isEqualTo(statusAt);
    }
}

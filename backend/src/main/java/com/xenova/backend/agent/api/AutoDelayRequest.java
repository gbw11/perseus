package com.xenova.backend.agent.api;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;

public record AutoDelayRequest(
        @JsonProperty("delay_ms") @NotNull @Min(1) @Max(100) Integer delayMs,
        @JsonProperty("duration_s") @NotNull @Min(1) @Max(120) Integer durationS) {
}

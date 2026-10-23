package com.xenova.backend.agent.application;

import com.xenova.backend.agent.domain.MetricSample;

// Published after a new metric sample is stored, so live listeners (WS-01) can forward it.
public record MetricSampleRecorded(MetricSample sample) {
}

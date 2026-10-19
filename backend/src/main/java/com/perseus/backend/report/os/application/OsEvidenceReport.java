package com.perseus.backend.report.os.application;

import com.perseus.backend.agent.domain.AgentStatus;
import com.perseus.backend.agent.domain.NetworkInterface;
import com.perseus.backend.template.domain.GuestKind;
import com.perseus.backend.vm.domain.VmState;
import java.time.Instant;
import java.util.List;

public record OsEvidenceReport(
        String reportId,
        String buildId,
        Instant generatedAt,
        ObservationWindow observationWindow,
        List<String> requiredOsIds,
        Verdict verdict,
        List<String> reasons,
        List<Evidence> entries) {

    public enum Verdict {
        PASS,
        FAIL,
        BLOCKED
    }

    // NOT_APPLICABLE: a zephyr guest has no agent, so there is no in-guest report to compare.
    public enum Match {
        MATCH,
        MISMATCH,
        UNKNOWN,
        NOT_APPLICABLE
    }

    public record ObservationWindow(Instant from, Instant to) {
    }

    public record ExpectedOs(String id, String version) {
    }

    public record ObservedOs(String id, String versionId, String prettyName, String kernelRelease) {
    }

    public record Evidence(
            String vmId,
            GuestKind guestKind,
            String runId,
            String bootId,
            VmState xenState,
            AgentStatus agentStatus,
            ExpectedOs expectedOs,
            ObservedOs observedOs,
            String hostname,
            List<NetworkInterface> interfaces,
            Instant observedAt,
            Instant receivedAt,
            Long freshnessMs,
            Match match) {
    }
}

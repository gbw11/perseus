package com.perseus.backend.report.os.application;

import com.perseus.backend.agent.application.AgentDataService;
import com.perseus.backend.agent.domain.AgentHeartbeat;
import com.perseus.backend.agent.domain.AgentInventory;
import com.perseus.backend.agent.domain.AgentStatus;
import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.report.os.application.OsEvidenceReport.Evidence;
import com.perseus.backend.report.os.application.OsEvidenceReport.ExpectedOs;
import com.perseus.backend.report.os.application.OsEvidenceReport.Match;
import com.perseus.backend.report.os.application.OsEvidenceReport.ObservationWindow;
import com.perseus.backend.report.os.application.OsEvidenceReport.ObservedOs;
import com.perseus.backend.report.os.application.OsEvidenceReport.Verdict;
import com.perseus.backend.report.os.infrastructure.OsEvidenceProperties;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.application.VmService;
import com.perseus.backend.vm.domain.VmState;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Service;

// Judged on request from the latest stored agent reports and the VM list (Spring records + live Xen state).
@Service
@EnableConfigurationProperties(OsEvidenceProperties.class)
public class OsEvidenceService {

    private final VmService vmService;
    private final AgentDataService agentDataService;
    private final OsEvidenceProperties properties;
    private final Clock clock;

    public OsEvidenceService(
            VmService vmService,
            AgentDataService agentDataService,
            OsEvidenceProperties properties,
            Clock clock) {
        this.vmService = vmService;
        this.agentDataService = agentDataService;
        this.properties = properties;
        this.clock = clock;
    }

    public OsEvidenceReport generate() {
        Instant now = clock.instant();
        ObservationWindow window = new ObservationWindow(now.minus(properties.observationWindow()), now);
        List<String> reasons = new ArrayList<>();

        List<Vm> vms;
        try {
            vms = vmService.list();
        } catch (ApiException exception) {
            reasons.add("VM 제어 서비스에서 VM 목록을 가져오지 못했습니다: " + exception.getMessage());
            return report(now, window, Verdict.BLOCKED, reasons, List.of());
        }

        List<Evidence> entries = vms.stream().map(vm -> evidenceOf(vm, now)).toList();
        Verdict verdict = Verdict.PASS;
        for (String osId : properties.requiredOsIds()) {
            Verdict osVerdict = judge(osId, entries, reasons);
            if (osVerdict == Verdict.FAIL || (osVerdict == Verdict.BLOCKED && verdict == Verdict.PASS)) {
                verdict = osVerdict;
            }
        }
        return report(now, window, verdict, reasons, entries);
    }

    private OsEvidenceReport report(
            Instant now, ObservationWindow window, Verdict verdict, List<String> reasons, List<Evidence> entries) {
        return new OsEvidenceReport(
                UUID.randomUUID().toString(),
                properties.buildId(),
                now,
                window,
                properties.requiredOsIds(),
                verdict,
                List.copyOf(reasons),
                entries);
    }

    private Evidence evidenceOf(Vm vm, Instant now) {
        AgentInventory inventory = agentDataService.findInventory(vm.vmId()).orElse(null);
        AgentHeartbeat heartbeat = agentDataService.findHeartbeat(vm.vmId()).orElse(null);
        AgentStatus agentStatus = agentDataService.statusOf(heartbeat, now);

        ObservedOs observedOs = inventory == null ? null : new ObservedOs(
                inventory.getOsId(), inventory.getOsVersionId(), inventory.getPrettyName(), inventory.getKernelRelease());
        Match match = matchOf(vm, inventory, heartbeat);

        return new Evidence(
                vm.vmId(),
                null,
                inventory == null ? null : inventory.getBootId(),
                vm.state(),
                agentStatus,
                new ExpectedOs(vm.templateId(), vm.templateVersion()),
                observedOs,
                inventory == null ? null : inventory.getHostname(),
                inventory == null ? List.of() : inventory.getInterfaces(),
                inventory == null ? null : inventory.getObservedAt(),
                inventory == null ? null : inventory.getReceivedAt(),
                heartbeat == null ? null : Duration.between(heartbeat.getReceivedAt(), now).toMillis(),
                match);
    }

    // An OS report from a previous boot says nothing about what is running now.
    private Match matchOf(Vm vm, AgentInventory inventory, AgentHeartbeat heartbeat) {
        if (inventory == null || inventory.getOsId() == null) {
            return Match.UNKNOWN;
        }
        if (heartbeat != null && heartbeat.getBootId() != null
                && !Objects.equals(heartbeat.getBootId(), inventory.getBootId())) {
            return Match.UNKNOWN;
        }
        return inventory.getOsId().equals(vm.templateId()) ? Match.MATCH : Match.MISMATCH;
    }

    private Verdict judge(String osId, List<Evidence> entries, List<String> reasons) {
        List<Evidence> candidates = entries.stream()
                .filter(entry -> osId.equals(entry.expectedOs().id()))
                .toList();
        if (candidates.isEmpty()) {
            reasons.add(osId + " 템플릿으로 만든 VM이 없습니다.");
            return Verdict.BLOCKED;
        }
        boolean verified = candidates.stream().anyMatch(entry -> entry.xenState() == VmState.RUNNING
                && entry.agentStatus() == AgentStatus.ONLINE
                && entry.match() == Match.MATCH);
        if (verified) {
            return Verdict.PASS;
        }
        candidates.stream()
                .filter(entry -> entry.match() == Match.MISMATCH)
                .findFirst()
                .ifPresent(entry -> reasons.add(osId + " VM(" + entry.vmId() + ")에서 다른 OS("
                        + entry.observedOs().id() + ")가 보고되었습니다."));
        if (candidates.stream().anyMatch(entry -> entry.match() == Match.MISMATCH)) {
            return Verdict.FAIL;
        }
        reasons.add(osId + " VM이 실행 중이 아니거나 에이전트 보고가 없거나 오래되었습니다.");
        return Verdict.BLOCKED;
    }
}

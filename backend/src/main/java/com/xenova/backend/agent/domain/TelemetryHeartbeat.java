package com.xenova.backend.agent.domain;

import com.xenova.backend.common.UnsignedDecimal;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.Instant;

/** Latest diagnostic observation per VM, separate from the v1 Agent liveness record. */
@Entity
@Table(name = "vm_telemetry_heartbeats")
public class TelemetryHeartbeat {

    @Id
    @Column(length = 36)
    private String vmId;

    @Version
    private Long revision;

    @Column(nullable = false, length = 36)
    private String bootId;

    @Column(nullable = false, length = 36)
    private String publisherInstanceId;

    @Column(length = 36)
    private String previousBootId;

    @Column(length = 36)
    private String previousPublisherInstanceId;

    @Column(nullable = false, length = 20)
    private String seq;

    @Column(length = 20)
    private String sentUptimeMs;

    @Column(nullable = false, length = 8)
    private String publisherState;

    @Column(length = 20)
    private String lastCollectionSuccessUptimeMs;

    @Column(length = 20)
    private String collectionFailuresTotal;

    @Column(length = 20)
    private String publishSubmitFailuresTotal;

    @Column(length = 20)
    private String connectionAttemptsTotal;

    @Column(nullable = false)
    private Instant receivedAt;

    protected TelemetryHeartbeat() {
    }

    public TelemetryHeartbeat(String vmId) {
        this.vmId = vmId;
    }

    public boolean observe(Report report) {
        if (receivedAt != null && report.receivedAt().isBefore(receivedAt)) {
            return false;
        }
        if (report.bootId().equals(bootId)) {
            if (report.publisherInstanceId().equals(publisherInstanceId)) {
                if (UnsignedDecimal.compare(report.seq(), seq) <= 0 || isOlderInSameBoot(report)) {
                    return false;
                }
            } else {
                if (report.publisherInstanceId().equals(previousPublisherInstanceId)
                        || isOlderInSameBoot(report)) {
                    return false;
                }
                previousPublisherInstanceId = publisherInstanceId;
            }
        } else {
            if (report.bootId().equals(previousBootId)) {
                return false;
            }
            previousBootId = bootId;
            previousPublisherInstanceId = null;
        }
        bootId = report.bootId();
        publisherInstanceId = report.publisherInstanceId();
        seq = report.seq();
        sentUptimeMs = report.sentUptimeMs();
        publisherState = report.publisherState();
        lastCollectionSuccessUptimeMs = report.lastCollectionSuccessUptimeMs();
        collectionFailuresTotal = report.collectionFailuresTotal();
        publishSubmitFailuresTotal = report.publishSubmitFailuresTotal();
        connectionAttemptsTotal = report.connectionAttemptsTotal();
        receivedAt = report.receivedAt();
        return true;
    }

    private boolean isOlderInSameBoot(Report report) {
        return report.sentUptimeMs() != null && sentUptimeMs != null
                && UnsignedDecimal.compare(report.sentUptimeMs(), sentUptimeMs) < 0;
    }

    public Report report() {
        return new Report(bootId, publisherInstanceId, seq, sentUptimeMs, publisherState,
                lastCollectionSuccessUptimeMs, collectionFailuresTotal, publishSubmitFailuresTotal,
                connectionAttemptsTotal, receivedAt);
    }

    public record Report(
            String bootId, String publisherInstanceId, String seq, String sentUptimeMs, String publisherState,
            String lastCollectionSuccessUptimeMs, String collectionFailuresTotal, String publishSubmitFailuresTotal,
            String connectionAttemptsTotal, Instant receivedAt) {
    }
}

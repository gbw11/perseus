package com.xenova.backend.event.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "recovery_sync_states")
public class RecoverySyncState {

    public static final String RECOVERY_EVENTS = "RECOVERY_EVENTS";

    @Id
    @Column(name = "scope_key", length = 50)
    private String scopeKey;

    @Column(name = "after_event_id", length = 36)
    private String afterEventId;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected RecoverySyncState() {
    }

    public RecoverySyncState(String scopeKey, Instant updatedAt) {
        this.scopeKey = scopeKey;
        this.updatedAt = updatedAt;
    }

    public void advance(String afterEventId, Instant updatedAt) {
        this.afterEventId = afterEventId;
        this.updatedAt = updatedAt;
    }

    public String getScopeKey() {
        return scopeKey;
    }

    public String getAfterEventId() {
        return afterEventId;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}

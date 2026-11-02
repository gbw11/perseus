CREATE TABLE recovery_sync_states (
    scope_key VARCHAR(50) NOT NULL,
    after_event_id VARCHAR(36),
    updated_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_recovery_sync_states PRIMARY KEY (scope_key)
);

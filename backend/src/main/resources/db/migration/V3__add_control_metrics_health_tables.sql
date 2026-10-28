RENAME TABLE metric_samples TO vm_metric_samples;
RENAME TABLE process_snapshots TO vm_process_snapshots;

ALTER TABLE vm_metric_samples
    RENAME INDEX idx_metric_samples_vm_observed TO idx_vm_metric_samples_vm_observed;

CREATE TABLE vehicle_control_states (
    id BIGINT NOT NULL AUTO_INCREMENT,
    scope_key VARCHAR(50) NOT NULL,
    desired_mode VARCHAR(30),
    active_mode VARCHAR(30),
    active_vm_id VARCHAR(36),
    active_app_id VARCHAR(100),
    generation BIGINT,
    applied_generation BIGINT,
    control_boot_id VARCHAR(64),
    control_instance_id VARCHAR(36),
    control_seq BIGINT,
    control_received_at TIMESTAMP(6),
    status_boot_id VARCHAR(64),
    status_instance_id VARCHAR(36),
    status_seq BIGINT,
    status_received_at TIMESTAMP(6),
    mode_changed_at TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_vehicle_control_states PRIMARY KEY (id),
    CONSTRAINT uk_vehicle_control_states_scope_key UNIQUE (scope_key)
);
CREATE INDEX idx_vehicle_control_states_active_vm_id ON vehicle_control_states (active_vm_id);

CREATE TABLE vm_recovery_health (
    vm_id VARCHAR(36) NOT NULL,
    last_event_id VARCHAR(36),
    recovery_id VARCHAR(36),
    recovery_attempt_id VARCHAR(36),
    status VARCHAR(20) NOT NULL,
    attempt INT,
    recoveries_total BIGINT NOT NULL,
    final_failures_total BIGINT NOT NULL,
    last_recovery_duration_ms BIGINT,
    last_occurred_at TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_vm_recovery_health PRIMARY KEY (vm_id),
    CONSTRAINT uk_vm_recovery_health_last_event_id UNIQUE (last_event_id)
);
CREATE INDEX idx_vm_recovery_health_status ON vm_recovery_health (status);

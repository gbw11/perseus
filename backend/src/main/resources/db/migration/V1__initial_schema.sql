CREATE TABLE users (
    id VARCHAR(36) NOT NULL,
    username VARCHAR(32) NOT NULL,
    nickname VARCHAR(50) NOT NULL,
    password_hash VARCHAR(100) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uk_users_username UNIQUE (username)
);

CREATE TABLE vms (
    vm_id VARCHAR(36) NOT NULL,
    name VARCHAR(63) NOT NULL,
    template_id VARCHAR(64) NOT NULL,
    template_version VARCHAR(64) NOT NULL,
    vcpus INT NOT NULL,
    memory_mib INT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    lifecycle VARCHAR(16),
    guest_kind VARCHAR(10),
    role VARCHAR(16),
    CONSTRAINT pk_vms PRIMARY KEY (vm_id),
    CONSTRAINT uk_vms_name UNIQUE (name)
);

CREATE TABLE refresh_tokens (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP(6) NOT NULL,
    revoked_at TIMESTAMP(6),
    created_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_refresh_tokens PRIMARY KEY (id),
    CONSTRAINT uk_refresh_tokens_token_hash UNIQUE (token_hash)
);
CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE revoked_access_tokens (
    jti VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    expires_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_revoked_access_tokens PRIMARY KEY (jti)
);
CREATE INDEX idx_revoked_access_tokens_user_id ON revoked_access_tokens (user_id);

CREATE TABLE agent_heartbeats (
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64),
    agent_instance_id VARCHAR(36),
    seq BIGINT,
    sent_at TIMESTAMP(6),
    received_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_agent_heartbeats PRIMARY KEY (vm_id)
);

CREATE TABLE agent_inventories (
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64),
    os_id VARCHAR(64),
    os_version_id VARCHAR(64),
    pretty_name VARCHAR(255),
    kernel_release VARCHAR(128),
    hostname VARCHAR(255),
    interfaces LONGTEXT,
    agent_version VARCHAR(64),
    observed_at TIMESTAMP(6),
    received_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_agent_inventories PRIMARY KEY (vm_id)
);

CREATE TABLE metric_samples (
    sample_id VARCHAR(36) NOT NULL,
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64),
    source VARCHAR(16) NOT NULL,
    observed_at TIMESTAMP(6) NOT NULL,
    received_at TIMESTAMP(6) NOT NULL,
    window_ms INT,
    metric_values LONGTEXT,
    CONSTRAINT pk_metric_samples PRIMARY KEY (sample_id)
);
CREATE INDEX idx_metric_samples_vm_observed ON metric_samples (vm_id, observed_at);

CREATE TABLE process_snapshots (
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64),
    observed_at TIMESTAMP(6) NOT NULL,
    received_at TIMESTAMP(6) NOT NULL,
    processes LONGTEXT,
    CONSTRAINT pk_process_snapshots PRIMARY KEY (vm_id)
);

CREATE TABLE reported_agent_statuses (
    vm_id VARCHAR(36) NOT NULL,
    status VARCHAR(10) NOT NULL,
    changed_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_reported_agent_statuses PRIMARY KEY (vm_id)
);

CREATE TABLE reported_vm_states (
    vm_id VARCHAR(36) NOT NULL,
    state VARCHAR(10) NOT NULL,
    changed_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_reported_vm_states PRIMARY KEY (vm_id)
);

CREATE TABLE vm_events (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_id VARCHAR(36) NOT NULL,
    type VARCHAR(40) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    vm_id VARCHAR(36),
    operation_id VARCHAR(36),
    occurred_at TIMESTAMP(6) NOT NULL,
    recorded_at TIMESTAMP(6) NOT NULL,
    payload VARCHAR(4000) NOT NULL,
    CONSTRAINT pk_vm_events PRIMARY KEY (id),
    CONSTRAINT uk_vm_events_event_id UNIQUE (event_id)
);
CREATE INDEX idx_vm_events_vm_id ON vm_events (vm_id);
CREATE INDEX idx_vm_events_recorded_at ON vm_events (recorded_at);

CREATE TABLE performance_trials (
    trial_id VARCHAR(36) NOT NULL,
    metric VARCHAR(32) NOT NULL,
    definition_id VARCHAR(64) NOT NULL,
    vm_id VARCHAR(36),
    source_vm_id VARCHAR(36),
    target_vm_id VARCHAR(36),
    build_id VARCHAR(64),
    template_version VARCHAR(64),
    operation_id VARCHAR(64),
    started_at TIMESTAMP(6) NOT NULL,
    finished_at TIMESTAMP(6),
    duration_ms BIGINT,
    success BOOLEAN NOT NULL,
    failure_reason VARCHAR(255),
    environment VARCHAR(16) NOT NULL,
    raw_log_ref VARCHAR(255),
    CONSTRAINT pk_performance_trials PRIMARY KEY (trial_id)
);

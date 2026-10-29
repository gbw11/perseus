CREATE TABLE users (
    id VARCHAR(36) NOT NULL,
    username VARCHAR(32) NOT NULL,
    nickname VARCHAR(50) NOT NULL,
    password_hash VARCHAR(100) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uk_users_username UNIQUE (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE vms (
    vm_id VARCHAR(36) NOT NULL,
    name VARCHAR(63) NOT NULL,
    template_id VARCHAR(64) NOT NULL,
    template_version VARCHAR(64) NOT NULL,
    vcpus INT NOT NULL,
    memory_mib INT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    lifecycle VARCHAR(16) NULL,
    guest_kind VARCHAR(10) NULL,
    role VARCHAR(16) NULL,
    CONSTRAINT pk_vms PRIMARY KEY (vm_id),
    CONSTRAINT uk_vms_name UNIQUE (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE refresh_tokens (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_refresh_tokens PRIMARY KEY (id),
    CONSTRAINT uk_refresh_tokens_token_hash UNIQUE (token_hash),
    INDEX idx_refresh_tokens_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE revoked_access_tokens (
    jti VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_revoked_access_tokens PRIMARY KEY (jti),
    INDEX idx_revoked_access_tokens_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE agent_heartbeats (
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64) NULL,
    agent_instance_id VARCHAR(36) NULL,
    seq BIGINT NULL,
    sent_at DATETIME(6) NULL,
    received_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_agent_heartbeats PRIMARY KEY (vm_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE agent_inventories (
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64) NULL,
    os_id VARCHAR(64) NULL,
    os_version_id VARCHAR(64) NULL,
    pretty_name VARCHAR(255) NULL,
    kernel_release VARCHAR(128) NULL,
    hostname VARCHAR(255) NULL,
    interfaces LONGTEXT NULL,
    agent_version VARCHAR(64) NULL,
    observed_at DATETIME(6) NULL,
    received_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_agent_inventories PRIMARY KEY (vm_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE metric_samples (
    sample_id VARCHAR(36) NOT NULL,
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64) NULL,
    source VARCHAR(16) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    received_at DATETIME(6) NOT NULL,
    window_ms INT NULL,
    metric_values LONGTEXT NULL,
    CONSTRAINT pk_metric_samples PRIMARY KEY (sample_id),
    INDEX idx_metric_samples_vm_observed (vm_id, observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE process_snapshots (
    vm_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(64) NULL,
    observed_at DATETIME(6) NOT NULL,
    received_at DATETIME(6) NOT NULL,
    processes LONGTEXT NULL,
    CONSTRAINT pk_process_snapshots PRIMARY KEY (vm_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE reported_agent_statuses (
    vm_id VARCHAR(36) NOT NULL,
    status VARCHAR(10) NOT NULL,
    changed_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_reported_agent_statuses PRIMARY KEY (vm_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE reported_vm_states (
    vm_id VARCHAR(36) NOT NULL,
    state VARCHAR(10) NOT NULL,
    changed_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_reported_vm_states PRIMARY KEY (vm_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE vm_events (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_id VARCHAR(36) NOT NULL,
    type VARCHAR(40) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    vm_id VARCHAR(36) NULL,
    operation_id VARCHAR(36) NULL,
    occurred_at DATETIME(6) NOT NULL,
    recorded_at DATETIME(6) NOT NULL,
    payload VARCHAR(4000) NOT NULL,
    CONSTRAINT pk_vm_events PRIMARY KEY (id),
    CONSTRAINT uk_vm_events_event_id UNIQUE (event_id),
    INDEX idx_vm_events_vm_id (vm_id),
    INDEX idx_vm_events_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE performance_trials (
    trial_id VARCHAR(36) NOT NULL,
    metric VARCHAR(32) NOT NULL,
    definition_id VARCHAR(64) NOT NULL,
    vm_id VARCHAR(36) NULL,
    source_vm_id VARCHAR(36) NULL,
    target_vm_id VARCHAR(36) NULL,
    build_id VARCHAR(64) NULL,
    template_version VARCHAR(64) NULL,
    operation_id VARCHAR(64) NULL,
    started_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    duration_ms BIGINT NULL,
    success BOOLEAN NOT NULL,
    failure_reason VARCHAR(255) NULL,
    environment VARCHAR(16) NOT NULL,
    raw_log_ref VARCHAR(255) NULL,
    CONSTRAINT pk_performance_trials PRIMARY KEY (trial_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

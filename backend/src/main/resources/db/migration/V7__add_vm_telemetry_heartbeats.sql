CREATE TABLE vm_telemetry_heartbeats (
    vm_id VARCHAR(36) NOT NULL,
    revision BIGINT NOT NULL,
    boot_id VARCHAR(36) NOT NULL,
    publisher_instance_id VARCHAR(36) NOT NULL,
    previous_boot_id VARCHAR(36),
    previous_publisher_instance_id VARCHAR(36),
    seq VARCHAR(20) NOT NULL,
    sent_uptime_ms VARCHAR(20),
    publisher_state VARCHAR(8) NOT NULL,
    last_collection_success_uptime_ms VARCHAR(20),
    collection_failures_total VARCHAR(20),
    publish_submit_failures_total VARCHAR(20),
    connection_attempts_total VARCHAR(20),
    received_at DATETIME(6) NOT NULL,
    CONSTRAINT pk_vm_telemetry_heartbeats PRIMARY KEY (vm_id)
);

ALTER TABLE vm_events
    ADD COLUMN correlation_id VARCHAR(36);

CREATE INDEX idx_vm_events_operation_id ON vm_events (operation_id);
CREATE INDEX idx_vm_events_correlation_id ON vm_events (correlation_id);

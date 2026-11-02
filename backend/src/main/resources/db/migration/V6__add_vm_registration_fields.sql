ALTER TABLE vms
    MODIFY COLUMN template_version VARCHAR(64) NULL,
    ADD COLUMN assigned_ipv4 VARCHAR(45);

-- Some deployed databases were baselined from an older Hibernate-generated
-- schema. In those databases these columns are still MySQL ENUMs, even though
-- V1 declares them as VARCHAR. Convert them so newly added application enum
-- values do not require a database ENUM alteration.
ALTER TABLE vms
    MODIFY COLUMN role VARCHAR(16) NULL;

ALTER TABLE vm_events
    MODIFY COLUMN type VARCHAR(40) NOT NULL;

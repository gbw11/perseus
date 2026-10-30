package com.xenova.backend;

import static org.assertj.core.api.Assertions.assertThat;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

@SpringBootTest
class XenovaBackendApplicationTests {

    @Autowired
    private Flyway flyway;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Test
    void flywayBuildsTheSchemaExpectedByHibernate() {
        assertThat(flyway.info().current().getVersion().getVersion()).isEqualTo("5");

        Integer tableCount = jdbcTemplate.queryForObject(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name IN (
                    'users', 'vms', 'vm_events', 'vehicle_control_states',
                    'vm_metric_samples', 'vm_process_snapshots', 'vm_recovery_health',
                    'recovery_sync_states'
                  )
                """,
                Integer.class);

        assertThat(tableCount).isEqualTo(8);
    }
}

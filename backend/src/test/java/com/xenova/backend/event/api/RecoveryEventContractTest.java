package com.xenova.backend.event.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.event.application.EventQueryService;
import com.xenova.backend.event.domain.EventType;
import java.util.List;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;

class RecoveryEventContractTest {
    @ParameterizedTest
    @ValueSource(strings = {"recovery.started", "recovery.completed", "recovery.recovered", "recovery.cancelled", "recovery.failed"})
    void acceptsEveryRecoveryOutcomeAsAnEventFilter(String value) throws Exception {
        EventQueryService service = mock(EventQueryService.class);
        when(service.find(any())).thenReturn(List.of());
        new EventController(service).list(null, value, null, null, null, null, 20);
        var query = ArgumentCaptor.forClass(EventQueryService.EventQuery.class);
        verify(service).find(query.capture());
        assertThat(query.getValue().type()).isEqualTo(EventType.fromValue(value).orElseThrow());
        assertThat(new ObjectMapper().writeValueAsString(query.getValue().type())).isEqualTo("\"" + value + "\"");
    }
}

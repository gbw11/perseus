package com.perseus.backend.metrics.application;

import com.perseus.backend.agent.domain.MetricSample;
import com.perseus.backend.agent.domain.MetricValue;
import com.perseus.backend.common.error.ApiException;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;

// Optional source and metric-name filters shared by REST-12 and WS-01.
public record MetricFilter(Set<String> sources, Set<String> names) {

    public static final Set<String> SOURCES = Set.of("xen", "guest_agent");

    public static MetricFilter of(List<String> sources, List<String> names) {
        Set<String> sourceSet = sources == null ? Set.of() : Set.copyOf(sources);
        for (String source : sourceSet) {
            if (!SOURCES.contains(source)) {
                throw new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "지원하지 않는 source입니다: " + source);
            }
        }
        return new MetricFilter(sourceSet, names == null ? Set.of() : Set.copyOf(names));
    }

    public static MetricFilter fromQuery(String source, String names) {
        return of(source == null ? null : List.of(source),
                names == null ? null : Arrays.stream(names.split(","))
                        .map(String::trim)
                        .filter(name -> !name.isEmpty())
                        .collect(Collectors.toList()));
    }

    public boolean acceptsSource(MetricSample sample) {
        return sources.isEmpty() || sources.contains(sample.getSource());
    }

    // Returns the values that pass the name filter, or an empty list when none do.
    public List<MetricValue> select(MetricSample sample) {
        if (names.isEmpty()) {
            return sample.getValues();
        }
        return sample.getValues().stream().filter(value -> names.contains(value.name())).toList();
    }
}

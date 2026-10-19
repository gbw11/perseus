package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.core.type.TypeReference;
import jakarta.persistence.Converter;

@Converter
public class MetricValuesConverter extends JsonListConverter<MetricValue> {

    public MetricValuesConverter() {
        super(new TypeReference<>() {
        });
    }
}

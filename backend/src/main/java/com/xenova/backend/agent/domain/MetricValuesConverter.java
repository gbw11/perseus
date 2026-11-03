package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;
import java.util.List;

@Converter
public class MetricValuesConverter implements AttributeConverter<MetricSampleData, String> {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final TypeReference<List<MetricValue>> VALUES = new TypeReference<>() {};

    @Override
    public String convertToDatabaseColumn(MetricSampleData data) {
        try {
            if (data == null || (data.network() == null && data.cpuWindow() == null)) {
                return MAPPER.writeValueAsString(data == null ? List.of() : data.values());
            }
            return MAPPER.writeValueAsString(data);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("Cannot serialize metric sample", exception);
        }
    }

    @Override
    public MetricSampleData convertToEntityAttribute(String json) {
        try {
            if (json == null || json.isBlank()) {
                return new MetricSampleData(List.of(), null, null);
            }
            if (json.stripLeading().startsWith("[")) {
                return new MetricSampleData(MAPPER.readValue(json, VALUES), null, null);
            }
            return MAPPER.readValue(json, MetricSampleData.class);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("Cannot deserialize metric sample", exception);
        }
    }
}

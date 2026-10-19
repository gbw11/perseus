package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.AttributeConverter;
import java.util.List;

// Lists that are only read back as a whole are stored as one JSON column.
abstract class JsonListConverter<T> implements AttributeConverter<List<T>, String> {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final TypeReference<List<T>> type;

    JsonListConverter(TypeReference<List<T>> type) {
        this.type = type;
    }

    @Override
    public String convertToDatabaseColumn(List<T> values) {
        try {
            return MAPPER.writeValueAsString(values == null ? List.of() : values);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("Cannot serialize JSON column", exception);
        }
    }

    @Override
    public List<T> convertToEntityAttribute(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return MAPPER.readValue(json, type);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("Cannot deserialize JSON column", exception);
        }
    }
}

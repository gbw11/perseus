package com.perseus.backend.agent.domain;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;
import java.util.List;

// Interfaces are only read back as a whole, so they are stored as one JSON column.
@Converter
public class NetworkInterfacesConverter implements AttributeConverter<List<NetworkInterface>, String> {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final TypeReference<List<NetworkInterface>> TYPE = new TypeReference<>() {
    };

    @Override
    public String convertToDatabaseColumn(List<NetworkInterface> interfaces) {
        try {
            return MAPPER.writeValueAsString(interfaces == null ? List.of() : interfaces);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("Cannot serialize network interfaces", exception);
        }
    }

    @Override
    public List<NetworkInterface> convertToEntityAttribute(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return MAPPER.readValue(json, TYPE);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("Cannot deserialize network interfaces", exception);
        }
    }
}

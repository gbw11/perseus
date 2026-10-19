package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.core.type.TypeReference;
import jakarta.persistence.Converter;

@Converter
public class NetworkInterfacesConverter extends JsonListConverter<NetworkInterface> {

    public NetworkInterfacesConverter() {
        super(new TypeReference<>() {
        });
    }
}

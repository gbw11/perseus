package com.perseus.backend.agent.domain;

import com.fasterxml.jackson.core.type.TypeReference;
import jakarta.persistence.Converter;

@Converter
public class ProcessInfosConverter extends JsonListConverter<ProcessInfo> {

    public ProcessInfosConverter() {
        super(new TypeReference<>() {
        });
    }
}

package com.xenova.backend.vm.infrastructure;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "xenova.vm-registration")
public record VmRegistrationProperties(String serviceToken) {
}


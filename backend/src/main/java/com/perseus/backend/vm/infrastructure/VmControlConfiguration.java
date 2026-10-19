package com.perseus.backend.vm.infrastructure;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(Dom0ManagerProperties.class)
public class VmControlConfiguration {
}

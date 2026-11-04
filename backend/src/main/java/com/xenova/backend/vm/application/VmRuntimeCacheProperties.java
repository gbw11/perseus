package com.xenova.backend.vm.application;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

// enabled: list/get answer from the last dom0-manager list and one background loop refreshes it.
// refreshDelay: pause after each refresh, so a state change shows up after about one dom0 call plus this.
// staleLimit: only a safety net for a stopped loop or failing refreshes; past it reads go to dom0-manager again.
@ConfigurationProperties(prefix = "xenova.vm.runtime-cache")
public record VmRuntimeCacheProperties(boolean enabled, Duration refreshDelay, Duration staleLimit) {
}

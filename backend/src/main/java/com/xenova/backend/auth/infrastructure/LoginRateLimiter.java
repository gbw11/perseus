package com.xenova.backend.auth.infrastructure;

import java.time.Clock;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;

@Component
public class LoginRateLimiter {

    private final AuthProperties properties;
    private final Clock clock;
    private final ConcurrentHashMap<String, Attempt> attempts = new ConcurrentHashMap<>();

    public LoginRateLimiter(AuthProperties properties, Clock clock) {
        this.properties = properties;
        this.clock = clock;
    }

    public boolean isBlocked(String username) {
        Attempt attempt = attempts.get(normalize(username));
        if (attempt == null) {
            return false;
        }
        if (attempt.windowStartedAt().plus(properties.loginAttemptWindow()).isBefore(clock.instant())) {
            attempts.remove(normalize(username), attempt);
            return false;
        }
        return attempt.count() >= properties.maxLoginAttempts();
    }

    public void recordFailure(String username) {
        String key = normalize(username);
        Instant now = clock.instant();
        attempts.compute(key, (ignored, attempt) -> {
            if (attempt == null || attempt.windowStartedAt().plus(properties.loginAttemptWindow()).isBefore(now)) {
                return new Attempt(1, now);
            }
            return new Attempt(attempt.count() + 1, attempt.windowStartedAt());
        });
    }

    public void reset(String username) {
        attempts.remove(normalize(username));
    }

    private String normalize(String username) {
        return username == null ? "" : username.toLowerCase();
    }

    private record Attempt(int count, Instant windowStartedAt) {
    }
}

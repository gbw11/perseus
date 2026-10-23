package com.xenova.backend.auth.infrastructure;

import com.xenova.backend.auth.domain.LoginAttemptStore;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "xenova.auth.state-store", havingValue = "jpa")
public class InMemoryLoginAttemptStore implements LoginAttemptStore {

    private final Clock clock;
    private final ConcurrentHashMap<String, Attempt> attempts = new ConcurrentHashMap<>();

    public InMemoryLoginAttemptStore(Clock clock) {
        this.clock = clock;
    }

    @Override
    public boolean isBlocked(String subjectHash, int maximumAttempts) {
        Attempt attempt = attempts.get(subjectHash);
        if (attempt == null) {
            return false;
        }
        if (!attempt.expiresAt().isAfter(clock.instant())) {
            attempts.remove(subjectHash, attempt);
            return false;
        }
        return attempt.count() >= maximumAttempts;
    }

    @Override
    public void recordFailure(String subjectHash, Duration window) {
        Instant now = clock.instant();
        attempts.compute(subjectHash, (ignored, attempt) -> {
            if (attempt == null || !attempt.expiresAt().isAfter(now)) {
                return new Attempt(1, now.plus(window));
            }
            return new Attempt(attempt.count() + 1, attempt.expiresAt());
        });
    }

    @Override
    public void reset(String subjectHash) {
        attempts.remove(subjectHash);
    }

    private record Attempt(int count, Instant expiresAt) {
    }
}

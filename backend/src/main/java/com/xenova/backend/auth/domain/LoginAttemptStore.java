package com.xenova.backend.auth.domain;

import java.time.Duration;

public interface LoginAttemptStore {

    boolean isBlocked(String subjectHash, int maximumAttempts);

    void recordFailure(String subjectHash, Duration window);

    void reset(String subjectHash);
}

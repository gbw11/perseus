package com.xenova.backend.auth.domain;

import java.time.Instant;

public interface RefreshTokenStore {

    void save(String userId, String tokenHash, Instant expiresAt, Instant createdAt);

    UseResult use(String tokenHash, Instant now);

    void revokeIfActive(String tokenHash, Instant now);

    record UseResult(Status status, String userId) {

        public static UseResult valid(String userId) {
            return new UseResult(Status.VALID, userId);
        }

        public static UseResult reused() {
            return new UseResult(Status.REUSED, null);
        }

        public static UseResult invalid() {
            return new UseResult(Status.INVALID, null);
        }
    }

    enum Status {
        VALID,
        REUSED,
        INVALID
    }
}

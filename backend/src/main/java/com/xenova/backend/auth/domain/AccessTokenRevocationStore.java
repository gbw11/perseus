package com.xenova.backend.auth.domain;

import java.time.Instant;

public interface AccessTokenRevocationStore {

    void revoke(String jti, String userId, Instant expiresAt);

    boolean isRevoked(String jti);
}

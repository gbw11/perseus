package com.xenova.backend.auth.api;

import java.time.Instant;

public record LoginResponse(
        String accessToken,
        String tokenType,
        long expiresIn,
        Instant expiresAt,
        UserResponse user) {

    public record UserResponse(String userId, String username) {
    }
}

package com.xenova.backend.auth.api;

import java.time.Instant;

public record SignupResponse(String userId, String username, String nickname, Instant createdAt) {
}

package com.perseus.backend.auth.api;

import java.time.Instant;

public record SignupResponse(String userId, String username, Instant createdAt) {
}

package com.xenova.backend.auth.infrastructure;

import com.xenova.backend.auth.domain.LoginAttemptStore;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import org.springframework.stereotype.Component;

@Component
public class LoginRateLimiter {

    private final AuthProperties properties;
    private final LoginAttemptStore store;

    public LoginRateLimiter(AuthProperties properties, LoginAttemptStore store) {
        this.properties = properties;
        this.store = store;
    }

    public boolean isBlocked(String username) {
        return store.isBlocked(subjectHash(username), properties.maxLoginAttempts());
    }

    public void recordFailure(String username) {
        store.recordFailure(subjectHash(username), properties.loginAttemptWindow());
    }

    public void reset(String username) {
        store.reset(subjectHash(username));
    }

    private String subjectHash(String username) {
        String normalized = username == null ? "" : username.toLowerCase(Locale.ROOT);
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(normalized.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is not available", exception);
        }
    }
}

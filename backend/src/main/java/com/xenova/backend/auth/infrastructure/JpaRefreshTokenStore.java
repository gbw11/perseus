package com.xenova.backend.auth.infrastructure;

import com.xenova.backend.auth.domain.RefreshToken;
import com.xenova.backend.auth.domain.RefreshTokenRepository;
import com.xenova.backend.auth.domain.RefreshTokenStore;
import java.time.Instant;
import java.util.UUID;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "xenova.auth.state-store", havingValue = "jpa")
public class JpaRefreshTokenStore implements RefreshTokenStore {

    private final RefreshTokenRepository repository;

    public JpaRefreshTokenStore(RefreshTokenRepository repository) {
        this.repository = repository;
    }

    @Override
    public void save(String userId, String tokenHash, Instant expiresAt, Instant createdAt) {
        repository.save(new RefreshToken(UUID.randomUUID().toString(), userId, tokenHash, expiresAt, createdAt));
    }

    @Override
    public UseResult use(String tokenHash, Instant now) {
        RefreshToken current = repository.findByTokenHashForUpdate(tokenHash).orElse(null);
        if (current == null) {
            return UseResult.invalid();
        }
        if (current.isRevoked()) {
            repository.findAllByUserIdAndRevokedAtIsNull(current.getUserId())
                    .forEach(token -> token.revoke(now));
            return UseResult.reused();
        }
        if (!current.isActive(now)) {
            return UseResult.invalid();
        }
        current.revoke(now);
        return UseResult.valid(current.getUserId());
    }

    @Override
    public void revokeIfActive(String tokenHash, Instant now) {
        repository.findByTokenHash(tokenHash)
                .filter(token -> token.isActive(now))
                .ifPresent(token -> token.revoke(now));
    }
}

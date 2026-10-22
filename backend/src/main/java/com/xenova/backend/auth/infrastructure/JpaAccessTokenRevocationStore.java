package com.xenova.backend.auth.infrastructure;

import com.xenova.backend.auth.domain.AccessTokenRevocationStore;
import com.xenova.backend.auth.domain.RevokedAccessToken;
import com.xenova.backend.auth.domain.RevokedAccessTokenRepository;
import java.time.Instant;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "xenova.auth.state-store", havingValue = "jpa")
public class JpaAccessTokenRevocationStore implements AccessTokenRevocationStore {

    private final RevokedAccessTokenRepository repository;

    public JpaAccessTokenRevocationStore(RevokedAccessTokenRepository repository) {
        this.repository = repository;
    }

    @Override
    public void revoke(String jti, String userId, Instant expiresAt) {
        repository.save(new RevokedAccessToken(jti, userId, expiresAt));
    }

    @Override
    public boolean isRevoked(String jti) {
        return repository.existsById(jti);
    }
}

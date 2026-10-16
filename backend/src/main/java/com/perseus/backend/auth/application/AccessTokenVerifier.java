package com.perseus.backend.auth.application;

import com.perseus.backend.auth.domain.RevokedAccessTokenRepository;
import com.perseus.backend.auth.infrastructure.JwtTokenService;
import java.time.Instant;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.stereotype.Component;

// Checks an access token outside the HTTP filter chain, e.g. in the first WebSocket message.
@Component
public class AccessTokenVerifier {

    private final JwtTokenService jwtTokenService;
    private final RevokedAccessTokenRepository revokedAccessTokenRepository;

    public AccessTokenVerifier(
            JwtTokenService jwtTokenService, RevokedAccessTokenRepository revokedAccessTokenRepository) {
        this.jwtTokenService = jwtTokenService;
        this.revokedAccessTokenRepository = revokedAccessTokenRepository;
    }

    public VerifiedToken verify(String token) {
        if (token == null || token.isBlank()) {
            throw new InvalidAccessTokenException("토큰이 없습니다.");
        }
        Jwt jwt;
        try {
            jwt = jwtTokenService.decode(token);
        } catch (JwtException exception) {
            throw new InvalidAccessTokenException("유효하지 않거나 만료된 토큰입니다.");
        }
        if (jwt.getId() == null || revokedAccessTokenRepository.existsById(jwt.getId())) {
            throw new InvalidAccessTokenException("폐기된 토큰입니다.");
        }
        return new VerifiedToken(jwt.getSubject(), jwt.getExpiresAt());
    }

    public record VerifiedToken(String userId, Instant expiresAt) {
    }

    public static class InvalidAccessTokenException extends RuntimeException {

        public InvalidAccessTokenException(String message) {
            super(message);
        }
    }
}

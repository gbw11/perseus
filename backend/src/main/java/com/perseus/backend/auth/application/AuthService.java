package com.perseus.backend.auth.application;

import com.perseus.backend.auth.api.LoginResponse;
import com.perseus.backend.auth.api.SignupResponse;
import com.perseus.backend.auth.domain.RefreshToken;
import com.perseus.backend.auth.domain.RefreshTokenRepository;
import com.perseus.backend.auth.domain.RevokedAccessToken;
import com.perseus.backend.auth.domain.RevokedAccessTokenRepository;
import com.perseus.backend.auth.domain.User;
import com.perseus.backend.auth.domain.UserRepository;
import com.perseus.backend.auth.infrastructure.AuthProperties;
import com.perseus.backend.auth.infrastructure.JwtTokenService;
import com.perseus.backend.auth.infrastructure.JwtTokenService.IssuedAccessToken;
import com.perseus.backend.auth.infrastructure.LoginRateLimiter;
import com.perseus.backend.common.error.ApiException;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AuthService {

    private final UserRepository userRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final RevokedAccessTokenRepository revokedAccessTokenRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenService jwtTokenService;
    private final LoginRateLimiter loginRateLimiter;
    private final AuthProperties properties;
    private final Clock clock;
    private final SecureRandom secureRandom = new SecureRandom();

    public AuthService(
            UserRepository userRepository,
            RefreshTokenRepository refreshTokenRepository,
            RevokedAccessTokenRepository revokedAccessTokenRepository,
            PasswordEncoder passwordEncoder,
            JwtTokenService jwtTokenService,
            LoginRateLimiter loginRateLimiter,
            AuthProperties properties,
            Clock clock) {
        this.userRepository = userRepository;
        this.refreshTokenRepository = refreshTokenRepository;
        this.revokedAccessTokenRepository = revokedAccessTokenRepository;
        this.passwordEncoder = passwordEncoder;
        this.jwtTokenService = jwtTokenService;
        this.loginRateLimiter = loginRateLimiter;
        this.properties = properties;
        this.clock = clock;
    }

    @Transactional
    public SignupResponse signup(String username, String password) {
        if (userRepository.existsByUsername(username)) {
            throw new ApiException(HttpStatus.CONFLICT, "USERNAME_ALREADY_EXISTS", "이미 사용 중인 사용자 이름입니다.");
        }

        Instant now = clock.instant();
        User user = userRepository.save(new User(
                UUID.randomUUID().toString(),
                username,
                passwordEncoder.encode(password),
                now));
        return new SignupResponse(user.getId(), user.getUsername(), user.getCreatedAt());
    }

    @Transactional
    public LoginResult login(String username, String password) {
        if (loginRateLimiter.isBlocked(username)) {
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "LOGIN_RATE_LIMITED", "로그인 시도 횟수를 초과했습니다.");
        }

        User user = userRepository.findByUsername(username).orElse(null);
        if (user == null || !passwordEncoder.matches(password, user.getPasswordHash())) {
            loginRateLimiter.recordFailure(username);
            throw new ApiException(HttpStatus.UNAUTHORIZED, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.");
        }
        loginRateLimiter.reset(username);

        IssuedAccessToken accessToken = jwtTokenService.issueAccessToken(user);
        String refreshTokenValue = createRefreshTokenValue();
        Instant now = clock.instant();
        Instant refreshExpiresAt = now.plus(properties.refreshTokenTtl());
        refreshTokenRepository.save(new RefreshToken(
                UUID.randomUUID().toString(),
                user.getId(),
                jwtTokenService.hash(refreshTokenValue),
                refreshExpiresAt,
                now));

        LoginResponse response = new LoginResponse(
                accessToken.value(),
                "Bearer",
                properties.accessTokenTtl().toSeconds(),
                accessToken.expiresAt(),
                new LoginResponse.UserResponse(user.getId(), user.getUsername()));
        return new LoginResult(response, refreshTokenValue);
    }

    @Transactional
    public void logout(String bearerToken, String refreshTokenValue) {
        Instant now = clock.instant();

        if (bearerToken != null && !bearerToken.isBlank()) {
            try {
                Jwt jwt = jwtTokenService.decode(bearerToken);
                if (jwt.getId() != null && jwt.getExpiresAt() != null) {
                    revokedAccessTokenRepository.save(new RevokedAccessToken(
                            jwt.getId(), jwt.getSubject(), jwt.getExpiresAt()));
                }
            } catch (JwtException ignored) {
                // Invalid or expired access tokens need no additional revocation record.
            }
        }

        if (refreshTokenValue != null && !refreshTokenValue.isBlank()) {
            refreshTokenRepository.findByTokenHash(jwtTokenService.hash(refreshTokenValue))
                    .filter(token -> token.isActive(now))
                    .ifPresent(token -> token.revoke(now));
        }
    }

    private String createRefreshTokenValue() {
        byte[] value = new byte[32];
        secureRandom.nextBytes(value);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    public record LoginResult(LoginResponse response, String refreshToken) {
    }
}

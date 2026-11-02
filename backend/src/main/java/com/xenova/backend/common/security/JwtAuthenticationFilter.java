package com.xenova.backend.common.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.domain.AccessTokenRevocationStore;
import com.xenova.backend.auth.infrastructure.JwtTokenService;
import com.xenova.backend.common.error.ErrorResponse;
import com.xenova.backend.vm.infrastructure.VmRegistrationAuthenticationFilter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtTokenService jwtTokenService;
    private final AccessTokenRevocationStore accessTokenRevocationStore;
    private final ObjectMapper objectMapper;

    public JwtAuthenticationFilter(
            JwtTokenService jwtTokenService,
            AccessTokenRevocationStore accessTokenRevocationStore,
            ObjectMapper objectMapper) {
        this.jwtTokenService = jwtTokenService;
        this.accessTokenRevocationStore = accessTokenRevocationStore;
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        String token = extractBearerToken(request);
        if (token == null) {
            filterChain.doFilter(request, response);
            return;
        }

        try {
            Jwt jwt = jwtTokenService.decode(token);
            if (jwt.getId() == null || accessTokenRevocationStore.isRevoked(jwt.getId())) {
                writeUnauthorized(response, "TOKEN_REVOKED", "폐기된 토큰입니다.");
                return;
            }

            UsernamePasswordAuthenticationToken authentication =
                    new UsernamePasswordAuthenticationToken(jwt.getSubject(), token, List.of());
            authentication.setDetails(jwt);
            SecurityContextHolder.getContext().setAuthentication(authentication);
            filterChain.doFilter(request, response);
        } catch (JwtException exception) {
            SecurityContextHolder.clearContext();
            writeUnauthorized(response, "INVALID_TOKEN", "유효하지 않거나 만료된 토큰입니다.");
        }
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return request.getRequestURI().startsWith("/api/v1/auth/")
                || VmRegistrationAuthenticationFilter.PATH.equals(request.getRequestURI());
    }

    private String extractBearerToken(HttpServletRequest request) {
        String authorization = request.getHeader(HttpHeaders.AUTHORIZATION);
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return null;
        }
        return authorization.substring(7);
    }

    private void writeUnauthorized(HttpServletResponse response, String code, String message) throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getOutputStream(), ErrorResponse.of(code, message));
    }
}

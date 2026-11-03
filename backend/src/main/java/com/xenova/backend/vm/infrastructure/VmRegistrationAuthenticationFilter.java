package com.xenova.backend.vm.infrastructure;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.common.error.ErrorResponse;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class VmRegistrationAuthenticationFilter extends OncePerRequestFilter {

    public static final String AUTHORITY = "VM_REGISTRATION";
    public static final String PATH = "/api/v1/vms/registrations";

    private final VmRegistrationProperties properties;
    private final ObjectMapper objectMapper;

    public VmRegistrationAuthenticationFilter(VmRegistrationProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        if (!StringUtils.hasText(properties.serviceToken())) {
            write(response, 503, "VM_REGISTRATION_NOT_CONFIGURED", "VM 등록 통지 인증이 설정되지 않았습니다.");
            return;
        }
        String authorization = request.getHeader(HttpHeaders.AUTHORIZATION);
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            write(response, 401, "UNAUTHORIZED", "보드 인증 토큰이 필요합니다.");
            return;
        }
        String supplied = authorization.substring(7);
        if (!MessageDigest.isEqual(supplied.getBytes(StandardCharsets.UTF_8),
                properties.serviceToken().getBytes(StandardCharsets.UTF_8))) {
            write(response, 403, "FORBIDDEN", "보드 인증 토큰이 올바르지 않습니다.");
            return;
        }
        UsernamePasswordAuthenticationToken authentication = new UsernamePasswordAuthenticationToken(
                "vm-registration-sender", supplied, List.of(new SimpleGrantedAuthority(AUTHORITY)));
        SecurityContextHolder.getContext().setAuthentication(authentication);
        filterChain.doFilter(request, response);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !PATH.equals(request.getRequestURI());
    }

    private void write(HttpServletResponse response, int status, String code, String message) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getOutputStream(), ErrorResponse.of(code, message));
    }
}


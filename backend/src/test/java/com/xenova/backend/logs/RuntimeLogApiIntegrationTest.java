package com.xenova.backend.logs;

import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.vm.infrastructure.Dom0ManagerClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import com.xenova.backend.common.security.SecurityConfig;
import com.xenova.backend.common.security.JwtAuthenticationFilter;
import com.xenova.backend.auth.infrastructure.JwtTokenService;
import com.xenova.backend.auth.domain.AccessTokenRevocationStore;
import org.springframework.security.oauth2.jwt.Jwt;
import java.time.Instant;

import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(RuntimeLogController.class)
@Import({SecurityConfig.class, JwtAuthenticationFilter.class})
class RuntimeLogApiIntegrationTest {
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    @MockitoBean Dom0ManagerClient client;
    @MockitoBean JwtTokenService jwtTokens;
    @MockitoBean AccessTokenRevocationStore revoked;
    String token;

    @BeforeEach
    void login() {
        token = "browser-log-token";
        when(jwtTokens.decode(token)).thenReturn(Jwt.withTokenValue(token).header("alg", "HS256")
                .subject("log-user").claim("jti", "log-token-id").issuedAt(Instant.now())
                .expiresAt(Instant.now().plusSeconds(300)).build());
    }

    @Test
    void logsAreAuthenticatedAndKeepCursorMetadata() throws Exception {
        mvc.perform(get("/api/v1/logs")).andExpect(status().isUnauthorized());
        when(client.runtimeLogs(anyMap(), eq(false))).thenReturn(json.readTree("""
            {"epoch":"log-store-one","items":[{"id":8,"source_id":"dom0","message":"service started"}],
             "next_cursor":8,"has_more":false,"history_truncated":true}
            """));
        mvc.perform(get("/api/v1/logs").param("after", "2").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].message").value("service started"))
                .andExpect(jsonPath("$.history_truncated").value(true)).andExpect(jsonPath("$.next_cursor").value(8));
    }

    @Test
    void invalidFilterAndPublicIngestionAreRejected() throws Exception {
        mvc.perform(get("/api/v1/logs").param("limit", "100000").header("Authorization", "Bearer " + token))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/api/v1/logs/ingest").contentType(MediaType.APPLICATION_JSON).content("{}")
                .header("Authorization", "Bearer " + token)).andExpect(status().is4xxClientError());
    }
}

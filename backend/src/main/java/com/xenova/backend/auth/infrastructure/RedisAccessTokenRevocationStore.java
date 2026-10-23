package com.xenova.backend.auth.infrastructure;

import com.xenova.backend.auth.domain.AccessTokenRevocationStore;
import java.time.Instant;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "xenova.auth.state-store", havingValue = "redis", matchIfMissing = true)
public class RedisAccessTokenRevocationStore implements AccessTokenRevocationStore {

    private static final String KEY_PREFIX = "xenova:auth:access:revoked:";
    private static final DefaultRedisScript<Long> REVOKE = new DefaultRedisScript<>("""
            local now = redis.call('TIME')
            if tonumber(ARGV[2]) <= tonumber(now[1]) then return 0 end
            redis.call('SET', KEYS[1], ARGV[1])
            redis.call('EXPIREAT', KEYS[1], ARGV[2])
            return 1
            """, Long.class);

    private final StringRedisTemplate redis;

    public RedisAccessTokenRevocationStore(StringRedisTemplate redis) {
        this.redis = redis;
    }

    @Override
    public void revoke(String jti, String userId, Instant expiresAt) {
        redis.execute(REVOKE, List.of(KEY_PREFIX + jti), userId, Long.toString(expiresAt.getEpochSecond()));
    }

    @Override
    public boolean isRevoked(String jti) {
        return Boolean.TRUE.equals(redis.hasKey(KEY_PREFIX + jti));
    }
}

package com.xenova.backend.auth.infrastructure;

import com.xenova.backend.auth.domain.RefreshTokenStore;
import java.time.Instant;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "xenova.auth.state-store", havingValue = "redis", matchIfMissing = true)
public class RedisRefreshTokenStore implements RefreshTokenStore {

    private static final String TOKEN_PREFIX = "xenova:auth:refresh:token:";
    private static final String USER_PREFIX = "xenova:auth:refresh:user:";

    private static final DefaultRedisScript<Long> SAVE = new DefaultRedisScript<>("""
            redis.call('HSET', KEYS[1],
              'userId', ARGV[1], 'expiresAt', ARGV[2], 'createdAt', ARGV[3], 'revokedAt', '')
            redis.call('EXPIREAT', KEYS[1], ARGV[2])
            redis.call('SADD', KEYS[2], ARGV[4])
            redis.call('EXPIREAT', KEYS[2], ARGV[2])
            return 1
            """, Long.class);

    private static final DefaultRedisScript<String> USE = new DefaultRedisScript<>("""
            if redis.call('EXISTS', KEYS[1]) == 0 then return 'INVALID|' end
            local userId = redis.call('HGET', KEYS[1], 'userId')
            local expiresAt = tonumber(redis.call('HGET', KEYS[1], 'expiresAt'))
            if not expiresAt or expiresAt <= tonumber(ARGV[1]) then
              redis.call('DEL', KEYS[1])
              if userId then redis.call('SREM', ARGV[2] .. userId, ARGV[4]) end
              return 'INVALID|'
            end
            local revokedAt = redis.call('HGET', KEYS[1], 'revokedAt')
            if revokedAt and revokedAt ~= '' then
              local userKey = ARGV[2] .. userId
              local hashes = redis.call('SMEMBERS', userKey)
              for _, hash in ipairs(hashes) do
                local tokenKey = ARGV[3] .. hash
                if redis.call('EXISTS', tokenKey) == 1 then
                  redis.call('HSET', tokenKey, 'revokedAt', ARGV[1])
                else
                  redis.call('SREM', userKey, hash)
                end
              end
              return 'REUSED|'
            end
            redis.call('HSET', KEYS[1], 'revokedAt', ARGV[1])
            return 'VALID|' .. userId
            """, String.class);

    private static final DefaultRedisScript<Long> REVOKE = new DefaultRedisScript<>("""
            if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
            local expiresAt = tonumber(redis.call('HGET', KEYS[1], 'expiresAt'))
            local revokedAt = redis.call('HGET', KEYS[1], 'revokedAt')
            if expiresAt and expiresAt > tonumber(ARGV[1]) and (not revokedAt or revokedAt == '') then
              redis.call('HSET', KEYS[1], 'revokedAt', ARGV[1])
              return 1
            end
            return 0
            """, Long.class);

    private final StringRedisTemplate redis;

    public RedisRefreshTokenStore(StringRedisTemplate redis) {
        this.redis = redis;
    }

    @Override
    public void save(String userId, String tokenHash, Instant expiresAt, Instant createdAt) {
        redis.execute(SAVE,
                List.of(tokenKey(tokenHash), USER_PREFIX + userId),
                userId,
                Long.toString(expiresAt.getEpochSecond()),
                Long.toString(createdAt.getEpochSecond()),
                tokenHash);
    }

    @Override
    public UseResult use(String tokenHash, Instant now) {
        String result = redis.execute(USE,
                List.of(tokenKey(tokenHash)),
                Long.toString(now.getEpochSecond()), USER_PREFIX, TOKEN_PREFIX, tokenHash);
        if (result == null || result.startsWith("INVALID|")) {
            return UseResult.invalid();
        }
        if (result.startsWith("REUSED|")) {
            return UseResult.reused();
        }
        if (result.startsWith("VALID|")) {
            return UseResult.valid(result.substring("VALID|".length()));
        }
        throw new IllegalStateException("Unexpected Redis refresh-token result: " + result);
    }

    @Override
    public void revokeIfActive(String tokenHash, Instant now) {
        redis.execute(REVOKE, List.of(tokenKey(tokenHash)), Long.toString(now.getEpochSecond()));
    }

    private String tokenKey(String tokenHash) {
        return TOKEN_PREFIX + tokenHash;
    }
}

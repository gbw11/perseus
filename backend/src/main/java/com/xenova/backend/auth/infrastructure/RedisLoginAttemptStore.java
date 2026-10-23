package com.xenova.backend.auth.infrastructure;

import com.xenova.backend.auth.domain.LoginAttemptStore;
import java.time.Duration;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "xenova.auth.state-store", havingValue = "redis", matchIfMissing = true)
public class RedisLoginAttemptStore implements LoginAttemptStore {

    private static final String KEY_PREFIX = "xenova:auth:login:failures:";
    private static final DefaultRedisScript<Long> RECORD_FAILURE = new DefaultRedisScript<>("""
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
            return count
            """, Long.class);

    private final StringRedisTemplate redis;

    public RedisLoginAttemptStore(StringRedisTemplate redis) {
        this.redis = redis;
    }

    @Override
    public boolean isBlocked(String subjectHash, int maximumAttempts) {
        String count = redis.opsForValue().get(key(subjectHash));
        return count != null && Long.parseLong(count) >= maximumAttempts;
    }

    @Override
    public void recordFailure(String subjectHash, Duration window) {
        redis.execute(RECORD_FAILURE, List.of(key(subjectHash)), Long.toString(window.toMillis()));
    }

    @Override
    public void reset(String subjectHash) {
        redis.delete(key(subjectHash));
    }

    private String key(String subjectHash) {
        return KEY_PREFIX + subjectHash;
    }
}

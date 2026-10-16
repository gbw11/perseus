package com.perseus.backend.agent.domain;

public record ProcessInfo(Integer pid, String name, Double cpuPercent, Long rssBytes) {
}

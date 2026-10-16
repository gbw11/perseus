package com.perseus.backend.agent.domain;

import java.util.List;

public record NetworkInterface(String mac, List<String> ips) {

    public NetworkInterface {
        ips = ips == null ? List.of() : List.copyOf(ips);
    }
}

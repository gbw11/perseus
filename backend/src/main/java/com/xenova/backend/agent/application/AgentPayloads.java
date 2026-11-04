package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.agent.domain.ProcessInfo;
import java.util.List;

final class AgentPayloads {

    private AgentPayloads() {
    }

    record Inventory(
            Os os,
            String kernelRelease,
            String hostname,
            List<Interface> interfaces,
            String agentVersion) {
    }

    record Os(String id, String versionId, String prettyName) {
    }

    record Interface(String name, String mac, List<Address> addresses) {
    }

    record Address(String address, String family) {
    }

    record Metrics(Integer windowMs, List<MetricValue> values, List<ProcessInfo> processes, JsonNode processCollection) {
    }
}

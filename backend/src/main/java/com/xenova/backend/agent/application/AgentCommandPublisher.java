package com.xenova.backend.agent.application;

/** Publishes MQTT-04 agent.command messages. */
public interface AgentCommandPublisher {

    /** False when the broker connection is down or the publish failed. */
    boolean publish(String topic, byte[] payload, int qos);
}

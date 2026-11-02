package com.xenova.backend.sim.application;

/** Publishes MQTT-17 sim.command. Implemented by the simulator relay's MQTT connection; absent when MQTT is off. */
public interface SimCommandPublisher {

    /** False when the broker connection is down or the publish failed. */
    boolean publish(String topic, byte[] payload, int qos);
}

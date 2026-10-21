package com.xenova.backend.drive.application;

// Sends one MQTT message. Implemented by the drive MQTT client; replaced by a fake in tests.
public interface DriveInputPublisher {

    /** @return false when the broker is not connected (the message is dropped, QoS 0 by contract). */
    boolean publish(String topic, byte[] payload, int qos);
}

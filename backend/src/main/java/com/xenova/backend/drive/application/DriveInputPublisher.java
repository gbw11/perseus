package com.xenova.backend.drive.application;

// Sends one MQTT message (manual input at QoS 0, resume requests at QoS 1). Implemented by the drive MQTT client; replaced by a fake in tests.
public interface DriveInputPublisher {

    /** @return false when the broker is not connected or the publish failed (the message is dropped). */
    boolean publish(String topic, byte[] payload, int qos);
}

package com.xenova.backend.agent.application;

// Clears the broker's retained agent.inventory of one VM (MQTT-02) by publishing an empty retained message.
// Implemented by the agent MQTT client; absent when MQTT is disabled.
public interface RetainedInventoryClearer {

    /** Never throws. A clear that cannot be sent now is logged and retried on the next (re)connect. */
    void clearRetainedInventory(String vmId);
}

package com.xenova.backend.drive.application;

import com.fasterxml.jackson.databind.JsonNode;

// An app event the relay stores in the event log (MQTT contract §6): drive.mode.changed, drive.fault.detected,
// drive.resume.accepted or drive.resume.cancelled.
// Recording happens off the MQTT callback thread, so the tracker only hands these out.
public record DriveNotice(String type, String publisher, JsonNode payload) {
}

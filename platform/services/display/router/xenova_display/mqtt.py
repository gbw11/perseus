"""Subscribe only; retained flag must survive normalization."""
import json
import logging

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)


class MqttSource:
    def __init__(self, store):
        self.store = store
        self.settings = store.settings
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"display-{store.epoch}", clean_session=True)
        if self.settings.mqtt_username:
            self.client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(1, 10)
        # Reading from a broker on loopback is the default; TLS on remote brokers is configured externally.

    def start(self):
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, 30)
        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            return
        with self.store.lock:
            self.store.mqtt_connected = True
        # Bindings are explicit, so no unbounded wildcard state is kept.
        for b in self.settings.bindings:
            if b.agent_supported:
                client.subscribe(f"xenova/v1/vms/{b.vm_id}/heartbeat", qos=1)
                client.subscribe(f"xenova/v1/vms/{b.vm_id}/inventory", qos=1)
            client.subscribe(f"xenova/v1/apps/{b.app_id}/events", qos=1)
        client.subscribe("xenova/v1/vehicle/control", qos=1)

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        with self.store.lock:
            self.store.mqtt_connected = False

    def on_message(self, client, userdata, message):
        if len(message.payload) > 65536:
            return
        try:
            envelope = json.loads(message.payload.decode("utf-8"))
            self.store.ingest(message.topic, envelope, retained=message.retain)
        except (ValueError, TypeError, KeyError, UnicodeError):
            log.debug("Rejected malformed display observation")

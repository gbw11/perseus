"""Subscribe only; retained flag must survive normalization."""
import json
import logging

import paho.mqtt.client as mqtt
from .config import DISPLAY_DRIVE_TOPIC

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
        self.client.on_subscribe = self.on_subscribe
        self.topics = tuple(dict.fromkeys([
            *[topic for b in self.settings.bindings if b.agent_supported for topic in
              (f"xenova/v1/vms/{b.vm_id}/heartbeat", f"xenova/v1/vms/{b.vm_id}/inventory")],
            *[f"xenova/v1/apps/{b.app_id}/events" for b in self.settings.bindings], DISPLAY_DRIVE_TOPIC]))
        self.pending = {}
        self.early = {}
        self.subscribing = False
        self.generation = 0
        self.client.reconnect_delay_set(1, 10)
        # Reading from a broker on loopback is the default; TLS on remote brokers is configured externally.

    def start(self):
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, 30)
        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        with self.store.lock:
            self.generation += 1
            generation = self.generation
            self.pending.clear(); self.early.clear()
            self.store.mqtt_connection(not reason_code.is_failure, self.topics)
            if reason_code.is_failure:
                return
            for topic in self.topics:
                self.subscribing = True
                try:
                    result, mid = client.subscribe(topic, qos=1)
                    if generation != self.generation or not self.store.mqtt_connected:
                        return
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        self.store.subscription(topic, "error")
                    elif mid in self.early:
                        self.store.subscription(topic, self.early.pop(mid))
                    else:
                        self.pending[mid] = topic
                except (OSError, RuntimeError, ValueError, TypeError):
                    self.store.subscription(topic, "error")
                finally:
                    self.subscribing = False
                    self.early.clear()

    def on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        with self.store.lock:
            if not self.store.mqtt_connected:
                return
            values = [getattr(code, "value", code) for code in (reason_codes or [])]
            status = "granted" if len(values) == 1 and values[0] in (0, 1, 2) else "denied"
            topic = self.pending.pop(mid, None)
            if topic is not None:
                self.store.subscription(topic, status)
            elif self.subscribing and len(self.early) < len(self.topics):
                # A callback may arrive before subscribe() has returned its mid.
                self.early[mid] = status

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        with self.store.lock:
            self.generation += 1
            self.pending.clear(); self.early.clear()
            self.store.mqtt_connection(False, self.topics)

    def on_message(self, client, userdata, message):
        if len(message.payload) > 65536:
            return
        try:
            envelope = json.loads(message.payload.decode("utf-8"))
            self.store.ingest(message.topic, envelope, retained=message.retain)
        except (ValueError, TypeError, KeyError, UnicodeError):
            log.debug("Rejected malformed display observation")

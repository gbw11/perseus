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
            *[b.app_topic(app) for b in self.settings.bindings for app in b.report_app_ids], DISPLAY_DRIVE_TOPIC]))
        self.pending = {}
        self.early = {}
        self.subscribing = False
        self.generation = 0
        self.inventory_requested = {}
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
            self.inventory_requested.clear()
            self.store.mqtt_connection(not reason_code.is_failure, self.topics)
            if reason_code.is_failure:
                return
            for topic in self.topics:
                if not self._subscribe(client, topic, generation):
                    return

    def _subscribe(self, client, topic, generation):
        self.subscribing = True
        try:
            result, mid = client.subscribe(topic, qos=1)
            if generation != self.generation or not self.store.mqtt_connected:
                return False
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
        return True

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
            self.inventory_requested.clear()
            self.store.mqtt_connection(False, self.topics)

    def on_message(self, client, userdata, message):
        if len(message.payload) > 65536:
            return
        try:
            envelope = json.loads(message.payload.decode("utf-8"))
            accepted = self.store.ingest(message.topic, envelope, retained=message.retain,
                                         qos=getattr(message, "qos", None), wire_size=len(message.payload))
            if accepted and client is not None and envelope['type'] == 'agent.heartbeat':
                with self.store.lock:
                    vm_id = envelope['vm_id']
                    guest = self.store.guests[vm_id]
                    agent_epoch = (envelope['boot_id'], envelope['agent_instance_id'])
                    epoch = (self.generation, guest['run_id'], *agent_epoch)
                    if self.inventory_requested.get(vm_id) == epoch:
                        return
                    self.inventory_requested[vm_id] = epoch
                    inventory = guest['channels'].get('agent.inventory')
                    if (inventory and inventory['source_epoch'] == list(agent_epoch)
                            and inventory.get('_mqtt_generation') == self.store.mqtt_generation):
                        return
                    # Initial retained inventory can precede the first verified
                    # Xen observation. Request its replay once after a live Agent
                    # is accepted; no command or publish permission is needed.
                    topic = f'xenova/v1/vms/{vm_id}/inventory'
                    self.store.subscription(topic, 'pending')
                    self._subscribe(client, topic, self.generation)
        except (ValueError, TypeError, KeyError, UnicodeError):
            log.debug("Rejected malformed display observation")

"""Board-local feed: shared manager observations and the existing MQTT receiver."""
import os
import uvicorn
from xenova_display.config import FeedSettings
from xenova_display.main import create_app
from dom0_manager.config import Settings
from dom0_manager.drivers import build_driver

settings = FeedSettings.from_env()
if not settings.mqtt_username or not settings.mqtt_password:
    raise ValueError('board display requires existing read-only MQTT credentials')
# Keep existing deployments on their current source until the manager producer
# and its final board freshness checks are ready. Shared mode never falls back.
driver = None if os.environ.get('XENOVA_OBSERVATIONS_PATH') else build_driver(Settings(driver='xen'))
app = create_app(settings, driver=driver)
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8082, log_level="warning", access_log=False)

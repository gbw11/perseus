"""Board-local feed, with real Xen reads and the existing MQTT receiver."""
import os
import uvicorn
from xenova_display.config import FeedSettings
from xenova_display.main import create_app
from dom0_manager.config import Settings
from dom0_manager.drivers import build_driver

app = create_app(FeedSettings.from_env(), driver=build_driver(Settings(driver="xen")))
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8082, log_level="warning", access_log=False)

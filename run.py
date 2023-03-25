#!/usr/local/bin/python3
import time
import urllib.request
import os
from pyShelly import pyShelly
shelly = pyShelly()
shelly.set_cloud_settings(os.environ.get('SHELLY_HOST'), os.environ.get('SHELLY_AUTH_KEY'), os.environ.get('SHELLY_PROMETHEUS_PORT'))
shelly.start()
while True:
    time.sleep(60)

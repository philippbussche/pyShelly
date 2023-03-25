# -*- coding: utf-8 -*-
# pylint: disable=broad-except, bare-except

import json
import time
try:
    import asyncio
except:
    pass
import threading
from datetime import datetime, timedelta

from .compat import s, uc, urlencode
from .const import LOGGER

import prometheus_client
import sys

try:
    import http.client as httplib
except:
    import httplib

class Cloud():
    def __init__(self, root, server, key, prometheus_port):
        self.auth_key = key
        self.server = server.replace("https://", "").replace("Server: ","")
        self._last_update = None
        self.update_interval = timedelta(minutes=1)
        self._device_list = None
        self._room_list = None
        self._cloud_thread = None
        self._last_post = datetime.now()
        self._root = root
        self.http_lock = threading.Lock()
        self.stopped = False
        self._prometheus_port = prometheus_port
        self._prometheus_server = None
        self._prometheus_metric_power_actual = None
        self._prometheus_metric_power_counter = None

    def __init_metrics(self):
        namespace = 'shelly'
        labelnames = ['room', 'device_label', 'device_type']
        
        if self._prometheus_metric_power_actual is None:
            self._prometheus_metric_power_actual = prometheus_client.Gauge(
                name='power_actual',
                documentation='Current real AC power being drawn (or injected), in Watts',
                labelnames=labelnames,
                namespace=namespace
            )

        if self._prometheus_metric_power_counter is None:
            self._prometheus_metric_power_counter = prometheus_client.Gauge(
                name='power_counter',
                documentation='Total real AC power being drawn (or injected), in Watt Hours',
                labelnames=labelnames,
                namespace=namespace
            )

    def start(self, cleanCache):
        if cleanCache:
            self._root.save_cache('cloud', {})
        self._cloud_thread = threading.Thread(target=self._update_loop)
        self._cloud_thread.name = "S4H-Cloud"
        self._cloud_thread.daemon = True
        self._cloud_thread.start()
        self.__init_metrics()
        try:
            self._prometheus_server = prometheus_client.start_http_server(int(self._prometheus_port))
        except Exception as e:
            LOGGER.fatal(
                "starting the http server on port '{}' failed with: {}".format(self._prometheus_port, str(e))
            )
            sys.exit(1)

    def stop(self):
       self.stopped = True

    def _update_loop(self):
        if self._root.event_loop:
            asyncio.set_event_loop(self._root.event_loop)
        try:
            cloud = self._root.load_cache('cloud')
            if cloud:
                self._device_list = cloud['device_list']
                self._room_list = cloud['room_list']
        except Exception as ex:
            LOGGER.error("Error load cloud cache, %s", ex)
        while not self._root.stopped.isSet() and not self.stopped:
            try:
                if self._last_update is None or \
                    datetime.now() - self._last_update \
                                    > self.update_interval:
                    self._last_update = datetime.now()
                    LOGGER.debug("Update from cloud")
                    devices = self.get_device_list()
                    if devices:
                        self._device_list = devices

                    rooms = self.get_room_list()
                    if rooms:
                        self._room_list = rooms

                    self._root.save_cache('cloud', \
                        {'device_list' : self._device_list,
                         'room_list' : self._room_list}
                    )
                    
                    self.collect()
                else:
                    self._root.stopped.wait(5)
            except Exception as ex:
                LOGGER.error("Error update cloud, %s", ex)

    def _post(self, path, params=None, retry=0):
        with self.http_lock:
            while datetime.now() - self._last_post < timedelta(seconds=2):
                time.sleep(1)
            self._last_post = datetime.now()

        json_body = None
        params = params or {}
        try:
            LOGGER.debug("POST to Shelly Cloud")
            conn = httplib.HTTPSConnection(self.server, timeout=15)
            headers = {'Content-Type' : 'application/x-www-form-urlencoded',
                        "Connection": "close"}
            params["auth_key"] = self.auth_key
            conn.request("POST", "/" + path, urlencode(params),
                         headers)
            resp = conn.getresponse()

            if resp.status == 200:
                body = resp.read()
                json_body = json.loads(s(body))
            else:
                if retry < 2:
                    return self._post(path, params, retry + 1)
                else:
                    LOGGER.warning("Error receive JSON from cloud, %s : %s", \
                                   resp.reason, resp.read())
        except Exception as ex:
            LOGGER.warning("Error connect cloud, %s", ex)
        finally:
            if conn:
                conn.close()

        return json_body

    def get_device_name(self, _id, idx=None, _ext_sensor=None):
        """Return name using template for device"""
        dev = None
        add_idx = idx and idx > 1
        if idx:
            dev = self._device_list.get(_id + '_' + str(idx-1))
            if dev:
                add_idx = False
        if not dev:
            dev = self._device_list.get(_id)
        if dev:
            name = dev['name']
            if _ext_sensor is not None and 'external_sensors_names' in dev:
                ext_sensors = dev['external_sensors_names']
                if str(_ext_sensor) in ext_sensors:
                    ext_name = ext_sensors[str(_ext_sensor)]['name']
                    if ext_name != 'unnamed':
                        name = ext_name
                        add_idx = False
            room = ""
            try:
                room_id = str(dev['room_id'])
                if room_id == '-10':
                    room = '[Hidden]'
                elif room_id in self._room_list:
                    room = self._room_list[room_id]['name']
                else:
                    room = str(room_id)
            except:
                pass
            tmpl = uc(self._root.tmpl_name)
            value = tmpl.format(id=id, name=name, room=room)
            if add_idx:
                value = value + " - " + str(idx)
            return value
        return None

    def get_relay_usage(self, _id, channel):
        dev_id = (_id + "_" + str(channel) if channel else _id).lower()
        if self._device_list and dev_id in self._device_list:
            dev = self._device_list[dev_id]
            if 'relay_usage' in dev:
                return dev['relay_usage']
        return None

    def get_room_name(self, _id):
        """Return room name of a device"""
        room = None
        if self._device_list and _id in self._device_list:
            dev = self._device_list[_id]
            try:
                room_id = str(dev['room_id'])
                if room_id == '-10':
                    room = '[Hidden]'
                elif room_id in self._room_list:
                    room = self._room_list[room_id]['name']
                else:
                    room = str(room_id)
            except:
                pass
        return room

    def get_device_list(self):
        resp = self._post("interface/device/list")
        return resp['data']['devices'] if resp else None

    def get_status(self):
        self._post("device/all_status")

    def get_room_list(self):
        resp = self._post("interface/room/list")
        return resp['data']['rooms'] if resp else None

    def get_device_status(self, _id):
        """Return status data for a device"""
        resp = self._post("device/status", { "id": _id })
        # return resp['data']['device_status']['meters'][0] if resp else None
        return resp['data']['device_status'] if resp else None

    def collect(self):
        """Collect metrics"""
        try:
            devices = self._device_list
            for id in devices:
                status = self.get_device_status(id)
                if meters:=status.get("meters"):
                    meter = meters[0]
                    power = meter.get("power")
                    total = meter.get("total")
                    room_name = self.get_room_name(id)
                    if power is not None:
                        print("Collecting Shelly power metrics (actual).")
                        self._prometheus_metric_power_actual.labels(room=room_name, device_label=devices[id]["name"], device_type=devices[id]["type"]).set(power)
                    if total is not None:
                        print("Collecting Shelly power metrics (counter).")
                        self._prometheus_metric_power_counter.labels(room=room_name, device_label=devices[id]["name"], device_type=devices[id]["type"]).set(float(total)/60)
        except Exception as e:
            LOGGER.warning(
                "collecting status from device(s) failed with: {1}".format(str(e))
            )
        finally:
            LOGGER.info('waiting {}s before next collection cycle'.format(self.update_interval))
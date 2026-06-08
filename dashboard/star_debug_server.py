#!/usr/bin/env python3
import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

# Add current directory to path for proto imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import grpc
import starlink_pb2
import starlink_pb2_grpc
from google.protobuf.json_format import MessageToDict

DISH_IP = "192.168.100.1"
DISH_PORT = 9200
ROUTER_IP = "192.168.1.1"
ROUTER_PORT = 9000

# Global state for mock mode simulation
mock_data = {
    "uptime_start": time.time(),
    "boot_count": 42,
    "stowed": False,
    "gps_sats": 12,
    "ping_history": [25, 30, 28, 35, 40, 32, 27],
    "throughput_down": 120.5,
    "throughput_up": 15.2,
    "obstruction_map": None,
    "clients": [
        {"mac_address": "12:34:56:78:9a:bc", "ip_address": "192.168.1.50", "given_name": "PC-Vincenzo", "snr": 0.0, "iface": "ETH"},
        {"mac_address": "fe:dc:ba:98:76:54", "ip_address": "192.168.1.102", "given_name": "Smartphone-User", "snr": 38.0, "iface": "RF_5GHZ"},
        {"mac_address": "8c:1d:e2:34:56:78", "ip_address": "192.168.1.105", "given_name": "Smart-TV", "snr": 25.0, "iface": "RF_2GHZ"}
    ],
    "historical_outages": [],
    "active_outage": None
}

def generate_mock_obstruction_map():
    import random
    rows, cols = 64, 64
    snr = []
    # Create a circular clear zone in the center
    for r in range(rows):
        for c in range(cols):
            dist_from_center = ((r - rows/2)**2 + (c - cols/2)**2)**0.5
            max_dist = (rows/2)
            if dist_from_center < max_dist * 0.7:
                snr.append(random.uniform(0.6, 1.0))
            elif dist_from_center < max_dist:
                snr.append(random.uniform(-0.2, 0.6))
            else:
                snr.append(random.uniform(-1.0, -0.6))
    return {
        "numRows": rows,
        "numCols": cols,
        "snr": snr
    }

mock_data["obstruction_map"] = generate_mock_obstruction_map()


class StarlinkBridge:
    def __init__(self, use_mock=False):
        self.use_mock = use_mock
        if use_mock:
            print("[INFO] Operating in Mock Mode. Live connection bypassed.")

    def query_device(self, host, port, request, timeout=1.5, raise_errors=False):
        """Helper to send a gRPC Request message to a Starlink device"""
        if self.use_mock:
            return None

        channel = grpc.insecure_channel(f"{host}:{port}")
        stub = starlink_pb2_grpc.DeviceStub(channel)
        try:
            response = stub.Handle(request, timeout=timeout)
            return MessageToDict(response, preserving_proto_field_name=True)
        except Exception as e:
            if raise_errors:
                raise e
            # Silence errors, return None so handler knows it's unreachable
            return None
        finally:
            channel.close()

    def get_dish_status(self):
        req = starlink_pb2.Request(get_status=starlink_pb2.GetStatusRequest())
        resp = self.query_device(DISH_IP, DISH_PORT, req)
        if resp:
            return {"reachable": True, "data": resp}

        if self.use_mock:
            return self.get_mock_dish_status()
        return {"reachable": False, "data": None}

    def get_dish_history(self):
        req = starlink_pb2.Request(get_history=starlink_pb2.GetHistoryRequest())
        resp = self.query_device(DISH_IP, DISH_PORT, req)
        if resp:
            return {"reachable": True, "data": resp}

        if self.use_mock:
            # Anchor mock timestamps to uptime_start so they remain stable across polls
            ref_time = mock_data["uptime_start"]
            now = time.time()
            mock_outages = [
                {
                    "cause": "OBSTRUCTED",
                    "start_timestamp_ns": str(int((ref_time - 3600) * 1000000000)),
                    "duration_ns": str(15 * 1000000000),
                    "did_switch": False
                },
                {
                    "cause": "NO_SATS",
                    "start_timestamp_ns": str(int((ref_time - 1800) * 1000000000)),
                    "duration_ns": str(8 * 1000000000),
                    "did_switch": False
                }
            ]
            # Add any dynamically generated historical outages
            mock_outages.extend(mock_data.get("historical_outages", []))

            active = mock_data.get("active_outage")
            if active:
                mock_outages.append({
                    "cause": active["cause"],
                    "start_timestamp_ns": str(int(active["start_time"] * 1000000000)),
                    "duration_ns": str(int((now - active["start_time"]) * 1000000000)),
                    "did_switch": active.get("did_switch", False)
                })

            return {
                "reachable": True,
                "data": {
                    "dish_get_history": {
                        "pop_ping_drop_rate": [0]*100,
                        "pop_ping_latency_ms": [35]*100,
                        "outages": mock_outages,
                        "event_log": {
                            "current_timestamp_ns": str(int(now * 1000000000)),
                            "events": [
                                {
                                    "severity": "EVENT_SEVERITY_WARNING",
                                    "reason": "EVENT_REASON_UT_ALERT_ETH_SLOW_LINK",
                                    "start_timestamp_ns": str(int((ref_time - 7200) * 1000000000)),
                                    "duration_ns": str(0)
                                }
                            ]
                        }
                    }
                }
            }
        return {"reachable": False, "data": None}

    def get_router_history(self):
        req = starlink_pb2.Request(get_history=starlink_pb2.GetHistoryRequest())
        resp = self.query_device(ROUTER_IP, ROUTER_PORT, req)
        if resp:
            return {"reachable": True, "data": resp}

        if self.use_mock:
            return self.get_mock_router_history()
        return {"reachable": False, "data": None}

    def get_mock_router_history(self):
        ref_time = mock_data["uptime_start"]
        now = time.time()
        events = [
            {
                "severity": "EVENT_SEVERITY_WARNING",
                "reason": "EVENT_REASON_CLIENT_RECONNECTING_OFTEN",
                "start_timestamp_ns": str(int((ref_time - 4000) * 1000000000)),
                "duration_ns": str(120 * 1000000000)
            },
            {
                "severity": "EVENT_SEVERITY_ADVISORY",
                "reason": "EVENT_REASON_CLIENT_SWITCHING_BAND",
                "start_timestamp_ns": str(int((ref_time - 2000) * 1000000000)),
                "duration_ns": str(5 * 1000000000)
            },
            {
                "severity": "EVENT_SEVERITY_WARNING",
                "reason": "EVENT_REASON_ROUTER_HIGH_OVERLAPPING_BSS",
                "start_timestamp_ns": str(int((ref_time - 800) * 1000000000)),
                "duration_ns": str(30 * 1000000000)
            }
        ]
        return {
            "reachable": True,
            "data": {
                "wifi_get_history": {
                    "event_log": {
                        "current_timestamp_ns": str(int(now * 1000000000)),
                        "events": events
                    }
                }
            }
        }

    def get_dish_obstruction_map(self):
        req = starlink_pb2.Request(dish_get_obstruction_map=starlink_pb2.DishGetObstructionMapRequest())
        resp = self.query_device(DISH_IP, DISH_PORT, req)
        if resp:
            # Extract dish_get_obstruction_map from response dict
            val = resp.get("dish_get_obstruction_map", {})
            return {"reachable": True, "data": val}

        if self.use_mock:
            return {"reachable": True, "data": mock_data["obstruction_map"]}
        return {"reachable": False, "data": None}

    def get_router_status(self):
        req = starlink_pb2.Request(get_status=starlink_pb2.GetStatusRequest())
        resp = self.query_device(ROUTER_IP, ROUTER_PORT, req)
        if resp:
            return {"reachable": True, "data": resp}

        if self.use_mock:
            return self.get_mock_router_status()
        return {"reachable": False, "data": None}

    def send_action(self, target, action_type, payload=None):
        """Executes reboot/stow/unstow actions"""
        if payload is None:
            payload = {}
        if self.use_mock:
            return self.execute_mock_action(target, action_type, payload)

        req = None
        host, port = DISH_IP, DISH_PORT
        if target == "router":
            host, port = ROUTER_IP, ROUTER_PORT

        if action_type == "reboot":
            req = starlink_pb2.Request(reboot=starlink_pb2.RebootRequest())
        elif action_type == "stow":
            req = starlink_pb2.Request(dish_stow=starlink_pb2.DishStowRequest(unstow=False))
        elif action_type == "unstow":
            req = starlink_pb2.Request(dish_stow=starlink_pb2.DishStowRequest(unstow=True))
        elif action_type == "inhibit_gps":
            req = starlink_pb2.Request(dish_inhibit_gps=starlink_pb2.DishInhibitGpsRequest(inhibit_gps=True))
        elif action_type == "allow_gps":
            req = starlink_pb2.Request(dish_inhibit_gps=starlink_pb2.DishInhibitGpsRequest(inhibit_gps=False))
        elif action_type == "inhibit_rf":
            req = starlink_pb2.Request(dish_inhibit_rf=starlink_pb2.DishInhibitRfRequest(inhibit_rf=True))
        elif action_type == "allow_rf":
            req = starlink_pb2.Request(dish_inhibit_rf=starlink_pb2.DishInhibitRfRequest(inhibit_rf=False))
        elif action_type == "clear_obstruction_map":
            req = starlink_pb2.Request(dish_clear_obstruction_map=starlink_pb2.DishClearObstructionMapRequest())
        elif action_type == "start_speedtest":
            req = starlink_pb2.Request(start_speedtest=starlink_pb2.StartSpeedtestRequest())

        elif action_type == "ping_host":
            host_val = payload.get("host", "8.8.8.8")
            req = starlink_pb2.Request(
                ping_host=starlink_pb2.PingHostRequest(address=host_val, size=64)
            )

        if not req:
            return {"success": False, "message": "Unknown action type"}

        timeout = 1.5
        if action_type == "ping_host":
            timeout = 10.0

        try:
            resp = self.query_device(host, port, req, timeout=timeout, raise_errors=True)
            if resp:
                return {"success": True, "response": resp}
            return {"success": False, "message": "Device unreachable"}
        except grpc.RpcError as rpc_err:
            status_code = rpc_err.code()
            details = rpc_err.details()
            if status_code == grpc.StatusCode.PERMISSION_DENIED:
                return {"success": False, "message": "Permission Denied: This action requires administrator authentication on the router."}
            elif status_code == grpc.StatusCode.UNIMPLEMENTED:
                return {"success": False, "message": f"Unimplemented: This action is not supported by the device ({details})."}
            elif status_code == grpc.StatusCode.DEADLINE_EXCEEDED:
                return {"success": False, "message": "Request timed out (Deadline Exceeded)."}
            else:
                return {"success": False, "message": f"gRPC Error ({status_code}): {details}"}
        except Exception as e:
            return {"success": False, "message": f"Error communicating with device: {str(e)}"}

    # Mock Data Generators
    def get_mock_dish_status(self):
        import random
        elapsed = int(time.time() - mock_data["uptime_start"])
        # Update mock metrics
        mock_data["throughput_down"] = max(10.0, mock_data["throughput_down"] + random.uniform(-10.0, 10.0))
        mock_data["throughput_up"] = max(1.0, mock_data["throughput_up"] + random.uniform(-2.0, 2.0))

        # Outage life-cycle simulation
        now = time.time()
        active = mock_data.get("active_outage")
        if active:
            # Check if active outage target duration is reached
            if now - active["start_time"] > active["target_duration"]:
                # End active outage and save to historical
                hist = {
                    "cause": active["cause"],
                    "start_timestamp_ns": str(int(active["start_time"] * 1000000000)),
                    "duration_ns": str(int(active["target_duration"] * 1000000000)),
                    "did_switch": active.get("did_switch", False)
                }
                mock_data["historical_outages"].append(hist)
                mock_data["active_outage"] = None
                active = None
        else:
            # Randomly trigger a new active outage (10% probability)
            if random.random() < 0.10:
                cause = random.choice(["OBSTRUCTED", "NO_SATS", "NO_PINGS", "SKY_SEARCH"])
                duration = random.uniform(5.0, 20.0) # 5 to 20 seconds
                mock_data["active_outage"] = {
                    "cause": cause,
                    "start_time": now,
                    "target_duration": duration,
                    "did_switch": random.choice([True, False])
                }
                active = mock_data["active_outage"]

        status = {
            "dish_get_status": {
                "device_info": {
                    "id": "ut-****************",
                    "hardware_version": "rev3_proto2",
                    "software_version": "191e4dfa-d63a-46b1-a73b-9fa907733864.uterm.release",
                    "bootcount": mock_data["boot_count"],
                    "country_code": "IT",
                },
                "device_state": {
                    "uptime_s": elapsed,
                },
                "alerts": {
                    "motors_stuck": False,
                    "thermal_throttle": False,
                    "thermal_shutdown": False,
                    "mast_not_near_vertical": False,
                    "unexpected_location": False,
                    "slow_ethernet_speeds": False,
                    "roaming": False,
                    "is_heating": active is not None and active["cause"] == "THERMAL_SHUTDOWN",
                },
                "gps_stats": {
                    "gps_valid": True,
                    "gps_sats": mock_data["gps_sats"]
                },
                "obstruction_stats": {
                    "currently_obstructed": active is not None and active["cause"] == "OBSTRUCTED",
                    "fraction_obstructed": 0.045,
                    "valid_s": elapsed
                },
                "stow_requested": mock_data["stowed"],
                "has_actuators": 1,
                "boresight_azimuth_deg": 12.4,
                "boresight_elevation_deg": 65.1,
                "eth_speed_mbps": 1000,
                "downlink_throughput_bps": 0.0 if active else mock_data["throughput_down"] * 1000000,
                "uplink_throughput_bps": 0.0 if active else mock_data["throughput_up"] * 1000000,
                "pop_ping_latency_ms": 0.0 if active else random.uniform(25.0, 38.0),
                "pop_ping_drop_rate": 1.0 if active else 0.0,
                "config": {
                    "snow_melt_mode": mock_data.get("snow_melt_mode", 0),
                    "swupdate_three_day_deferral_enabled": mock_data.get("swupdate_three_day_deferral_enabled", False),
                    "swupdate_reboot_hour": mock_data.get("swupdate_reboot_hour", 3),
                    "power_save_duration_minutes": 0,
                    "power_save_mode": False
                }
            }
        }

        # If there's an active outage, populate the "outage" field in status
        if active:
            status["dish_get_status"]["outage"] = {
                "cause": active["cause"],
                "start_timestamp_ns": str(int(active["start_time"] * 1000000000)),
                "duration_ns": str(int((now - active["start_time"]) * 1000000000)),
                "did_switch": active.get("did_switch", False)
            }

        return {"reachable": True, "data": status}

    def get_mock_router_status(self):
        import random
        elapsed = int(time.time() - mock_data["uptime_start"])
        status = {
            "wifi_get_status": {
                "device_info": {
                    "id": "Router-****************",
                    "hardware_version": "v2",
                    "software_version": "2024.12.0.mr32145",
                    "bootcount": 5
                },
                "device_state": {
                    "uptime_s": elapsed
                },
                "alerts": {
                    "thermal_throttle": False,
                    "wan_eth_poor_connection": False
                },
                "ipv4_wan_address": "100.89.XXX.XXX",
                "captive_portal_enabled": False,
                "poe_stats": {
                    "poe_state": "POE_STATE_ON",
                    "poe_power": 135.0 + random.uniform(-4.0, 4.0),
                    "vsns_vin": 56.2 + random.uniform(-0.25, 0.25)
                },
                "clients": mock_data["clients"]
            }
        }
        return {"reachable": True, "data": status}

    def get_wifi_config(self):
        if self.use_mock:
            return self.get_mock_wifi_config()

        req = starlink_pb2.Request(wifi_get_config=starlink_pb2.WifiGetConfigRequest())
        resp = self.query_device(ROUTER_IP, ROUTER_PORT, req)
        if resp:
            return {"reachable": True, "data": resp}
        return {"reachable": False, "data": None}

    def get_mock_wifi_config(self):
        return {
            "reachable": True,
            "data": {
                "wifi_get_config": {
                    "wifi_config": {
                        "channel_2ghz": 6,
                        "channel_5ghz": 44,
                        "networks": [
                            {
                                "basic_service_sets": [
                                    {
                                        "ssid": "Starlink_Home",
                                        "band": "RF_2GHZ",
                                        "auth_wpa2": {"password": "SuperSecurePassword123"}
                                    },
                                    {
                                        "ssid": "Starlink_Home",
                                        "band": "RF_5GHZ",
                                        "auth_wpa2": {"password": "SuperSecurePassword123"}
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }

    def get_speedtest_status(self):
        if self.use_mock:
            return self.get_mock_speedtest_status()

        req = starlink_pb2.Request(get_speedtest_status=starlink_pb2.GetSpeedtestStatusRequest())
        resp = self.query_device(ROUTER_IP, ROUTER_PORT, req)
        if resp:
            return {"reachable": True, "data": resp}
        return {"reachable": False, "data": None}

    def get_mock_speedtest_status(self):
        st = mock_data.get("speedtest")
        if not st or not st.get("running"):
            return {
                "reachable": True,
                "data": {
                    "get_speedtest_status": {
                        "status": {
                            "running": False,
                            "id": "mock-speedtest",
                            "down": {"throughputs_mbps": []},
                            "up": {"throughputs_mbps": []}
                        }
                    }
                }
            }

        elapsed = time.time() - st["start_time"]
        import random

        # Simulate 16 seconds speedtest: download (0-8s), upload (8-16s)
        if elapsed < 8:
            # Running download
            st["down"].append(random.uniform(120.0, 240.0))
        elif elapsed < 16:
            # Running upload
            st["up"].append(random.uniform(15.0, 35.0))
        else:
            st["running"] = False

        return {
            "reachable": True,
            "data": {
                "get_speedtest_status": {
                    "status": {
                        "running": st["running"],
                        "id": "mock-speedtest",
                        "down": {"throughputs_mbps": st["down"]},
                        "up": {"throughputs_mbps": st["up"]}
                    }
                }
            }
        }

    def execute_mock_action(self, target, action, payload=None):
        if payload is None:
            payload = {}
        if action == "reboot":
            mock_data["uptime_start"] = time.time()
            mock_data["boot_count"] += 1
            return {"success": True, "message": "Mock reboot initiated successfully"}
        elif action == "stow":
            mock_data["stowed"] = True
            return {"success": True, "message": "Mock stow request sent"}
        elif action == "unstow":
            mock_data["stowed"] = False
            return {"success": True, "message": "Mock unstow request sent"}
        elif action == "clear_obstruction_map":
            # Reset mock obstruction map to a fully clear state
            mock_data["obstruction_map"] = {
                "numRows": 64,
                "numCols": 64,
                "snr": [1.0] * (64 * 64)
            }
            return {"success": True, "message": "Mock obstruction map cleared successfully"}
        elif action == "start_speedtest":
            mock_data["speedtest"] = {
                "running": True,
                "start_time": time.time(),
                "down": [],
                "up": []
            }
            return {"success": True, "message": "Mock speed test started"}

        elif action == "ping_host":
            host = payload.get("host", "8.8.8.8")
            return {
                "success": True,
                "response": {
                    "ping_host": {
                        "result": {
                            "dropRate": 0.0,
                            "latencyMs": 24.5
                        }
                    }
                }
            }
        return {"success": True, "message": f"Mock action '{action}' completed successfully"}


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    bridge = None

    def log_message(self, format, *args):
        # Suppress logging every static asset request to keep console readable
        if args and isinstance(args[0], str) and ("GET /api/" in args[0] or "POST /api/" in args[0]):
            super().log_message(format, *args)

    def do_GET(self):
        # Strip query parameters for routing
        path = self.path
        if "?" in path:
            path = path.split("?")[0]

        if path == "/" or path == "/index.html":
            self.serve_file("index.html", "text/html")
        elif path == "/api/live/status":
            self.send_json({
                "dish": self.bridge.get_dish_status(),
                "router": self.bridge.get_router_status(),
                "history": self.bridge.get_dish_history(),
                "router_history": self.bridge.get_router_history(),
                "mock_mode": self.bridge.use_mock
            })
        elif path == "/api/live/wifi_config":
            self.send_json(self.bridge.get_wifi_config())
        elif path == "/api/live/speedtest_status":
            self.send_json(self.bridge.get_speedtest_status())
        elif path == "/api/live/obstruction_map":
            self.send_json(self.bridge.get_dish_obstruction_map())
        elif path.startswith("/api/live/mock_toggle"):
            # Utility to toggle mock mode in real-time
            self.bridge.use_mock = not self.bridge.use_mock
            self.send_json({"mock_mode": self.bridge.use_mock})
        else:
            # Try to serve as static file from dashboard directory
            file_name = path.lstrip("/")
            base_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.realpath(os.path.join(base_dir, file_name))
            base_real = os.path.realpath(base_dir)

            if not os.path.normcase(file_path).startswith(os.path.normcase(base_real) + os.sep):
                self.send_error(403, "Forbidden")
                return

            if os.path.exists(file_path) and os.path.isfile(file_path):
                if file_name.endswith(".js"):
                    mime = "application/javascript"
                elif file_name.endswith(".css"):
                    mime = "text/css"
                elif file_name.endswith(".png"):
                    mime = "image/png"
                elif file_name.endswith(".jpg"):
                    mime = "image/jpeg"
                elif file_name.endswith(".json"):
                    mime = "application/json"
                else:
                    mime = "text/plain"
                self.serve_file(file_name, mime)
            else:
                self.send_error(404, "File Not Found")

    def do_POST(self):
        if self.path == "/api/live/action":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                target = data.get("target")
                action = data.get("action")
                result = self.bridge.send_action(target, action, data)
                self.send_json(result)
            except Exception as e:
                self.send_json({"success": False, "message": str(e)}, status=400)
        else:
            self.send_error(404)

    def serve_file(self, filename, content_type):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        base_real = os.path.realpath(base_dir)
        path = os.path.realpath(os.path.join(base_dir, filename))
        if not os.path.normcase(path).startswith(os.path.normcase(base_real) + os.sep):
            self.send_error(403, "Forbidden")
            return
        try:
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def send_json(self, data, status=200):
        try:
            body = json.dumps(data).encode('utf-8')
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, f"JSON serialization error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Starlink PC Dashboard Server")
    parser.add_argument("--port", type=int, default=8000, help="Port to run HTTP server on")
    parser.add_argument("--mock", action="store_true", help="Force mock data simulation mode")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open web browser")
    args = parser.parse_args()

    bridge = StarlinkBridge(use_mock=args.mock)
    DashboardHTTPHandler.bridge = bridge

    server_address = ('', args.port)
    httpd = HTTPServer(server_address, DashboardHTTPHandler)

    print("============================================================")
    print("   STARLINK DEBUG DASHBOARD SERVER RUNNING")
    print(f"   Frontend available at: http://localhost:{args.port}/")
    print(f"   gRPC Target Dish: {DISH_IP}:{DISH_PORT}")
    print(f"   gRPC Target Router: {ROUTER_IP}:{ROUTER_PORT}")
    print("============================================================")

    # Auto open browser
    if not args.no_browser:
        def open_browser():
            time.sleep(1.0)
            print("[INFO] Opening dashboard in your default browser...")
            webbrowser.open(f"http://localhost:{args.port}/")
        threading.Thread(target=open_browser, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Server shutting down.")
        httpd.server_close()


if __name__ == "__main__":
    main()

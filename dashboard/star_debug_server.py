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
    "obstruction_map": None
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

    def query_device(self, host, port, request, timeout=1.5):
        """Helper to send a gRPC Request message to a Starlink device"""
        if self.use_mock:
            return None

        channel = grpc.insecure_channel(f"{host}:{port}")
        stub = starlink_pb2_grpc.DeviceStub(channel)
        try:
            response = stub.Handle(request, timeout=timeout)
            return MessageToDict(response, preserving_proto_field_name=True)
        except Exception:
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
            return {"reachable": True, "data": {"dish_get_history": {"pop_ping_drop_rate": [0]*100, "pop_ping_latency_ms": [35]*100}}}
        return {"reachable": False, "data": None}

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

    def send_action(self, target, action_type):
        """Executes reboot/stow/unstow actions"""
        if self.use_mock:
            return self.execute_mock_action(target, action_type)

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

        if not req:
            return {"success": False, "message": "Unknown action type"}

        resp = self.query_device(host, port, req)
        if resp:
            return {"success": True, "response": resp}
        return {"success": False, "message": "Device unreachable"}

    # Mock Data Generators
    def get_mock_dish_status(self):
        import random
        elapsed = int(time.time() - mock_data["uptime_start"])
        # Update mock metrics
        mock_data["throughput_down"] = max(10.0, mock_data["throughput_down"] + random.uniform(-10.0, 10.0))
        mock_data["throughput_up"] = max(1.0, mock_data["throughput_up"] + random.uniform(-2.0, 2.0))

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
                    "is_heating": False,
                },
                "gps_stats": {
                    "gps_valid": True,
                    "gps_sats": mock_data["gps_sats"]
                },
                "obstruction_stats": {
                    "currently_obstructed": False,
                    "fraction_obstructed": 0.045,
                    "valid_s": elapsed
                },
                "stow_requested": mock_data["stowed"],
                "has_actuators": 1,
                "boresight_azimuth_deg": 12.4,
                "boresight_elevation_deg": 65.1,
                "eth_speed_mbps": 1000,
                "downlink_throughput_bps": mock_data["throughput_down"] * 1000000,
                "uplink_throughput_bps": mock_data["throughput_up"] * 1000000,
                "pop_ping_latency_ms": random.uniform(25.0, 38.0),
                "pop_ping_drop_rate": 0.0,
                "config": {
                    "snow_melt_mode": mock_data.get("snow_melt_mode", 0),
                    "swupdate_three_day_deferral_enabled": mock_data.get("swupdate_three_day_deferral_enabled", False),
                    "swupdate_reboot_hour": mock_data.get("swupdate_reboot_hour", 3),
                    "power_save_duration_minutes": 0,
                    "power_save_mode": False
                }
            }
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
                "clients": [
                    {"mac_address": "12:34:56:78:9a:bc", "ip_address": "192.168.1.50", "given_name": "PC-Vincenzo", "snr": 0.0, "iface": "ETH"},
                    {"mac_address": "fe:dc:ba:98:76:54", "ip_address": "192.168.1.102", "given_name": "Smartphone-User", "snr": 38.0, "iface": "RF_5GHZ"},
                    {"mac_address": "8c:1d:e2:34:56:78", "ip_address": "192.168.1.105", "given_name": "Smart-TV", "snr": 25.0, "iface": "RF_2GHZ"}
                ]
            }
        }
        return {"reachable": True, "data": status}

    def execute_mock_action(self, target, action):
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
                "mock_mode": self.bridge.use_mock
            })
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
                result = self.bridge.send_action(target, action)
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

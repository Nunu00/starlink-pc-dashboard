import json
import threading
from http.server import HTTPServer

import pytest
from playwright.sync_api import Page

from star_debug_server import DashboardHTTPHandler, StarlinkBridge

XSS = "<img src=x onerror=alert(1)>"


@pytest.fixture(scope="module")
def server_port():
    DashboardHTTPHandler.bridge = StarlinkBridge(use_mock=True)
    DashboardHTTPHandler.log_message = lambda *args: None
    httpd = HTTPServer(("127.0.0.1", 0), DashboardHTTPHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port
    httpd.shutdown()


def _status_json(clients: list) -> str:
    return json.dumps({
        "dish": {"reachable": True, "data": {"dish_get_status": {}}},
        "router": {
            "reachable": True,
            "data": {"wifi_get_status": {"clients": clients}},
        },
        "history": {"reachable": False, "data": None},
        "mock_mode": True,
    })


def _setup(page: Page, port: int, clients: list) -> None:
    body = _status_json(clients)
    page.route(
        "**/api/live/status**",
        lambda route, _req: route.fulfill(
            status=200,
            content_type="application/json",
            body=body,
        ),
    )
    page.goto(f"http://127.0.0.1:{port}/")


class TestDisplayClientsXSS:
    def test_given_name_is_escaped(self, page: Page, server_port: int):
        _setup(page, server_port, [
            {"given_name": XSS, "ip_address": "1.2.3.4", "mac_address": "aa:bb:cc:dd:ee:ff", "snr": 30.0, "iface": "ETH"},
        ])
        page.wait_for_selector(".client-card", state="attached")

        title_text = page.locator(".client-title").first.text_content()
        assert XSS in title_text
        assert page.locator("img[src='x']").count() == 0

    def test_ip_address_is_escaped(self, page: Page, server_port: int):
        _setup(page, server_port, [
            {"given_name": "OK", "ip_address": XSS, "mac_address": "aa:bb:cc:dd:ee:ff", "snr": 30.0, "iface": "ETH"},
        ])
        page.wait_for_selector(".client-card", state="attached")

        sub_text = page.locator(".client-sub").first.text_content()
        assert XSS in sub_text
        assert page.locator("img[src='x']").count() == 0

    def test_mac_address_is_escaped(self, page: Page, server_port: int):
        _setup(page, server_port, [
            {"given_name": "OK", "ip_address": "1.2.3.4", "mac_address": XSS, "snr": 30.0, "iface": "ETH"},
        ])
        page.wait_for_selector(".client-card", state="attached")

        sub_text = page.locator(".client-sub").first.text_content()
        assert XSS in sub_text
        assert page.locator("img[src='x']").count() == 0

    def test_band_label_is_escaped(self, page: Page, server_port: int):
        # No iface key → client.iface is undefined in JS → getClientConnectionInfo
        # falls through to client.band as conn.label → esc(conn.label) in template
        _setup(page, server_port, [
            {"given_name": "OK", "ip_address": "1.2.3.4", "mac_address": "aa:bb", "snr": 0, "band": XSS},
        ])
        page.wait_for_selector(".client-card", state="attached")

        card_text = page.locator(".client-card").first.text_content()
        assert XSS in card_text
        assert page.locator("img[src='x']").count() == 0

    def test_non_numeric_snr_renders_as_zero_db(self, page: Page, server_port: int):
        # snr is coerced: Number(XSS) → NaN → NaN || 0 → 0, rendered as "0 dB"
        _setup(page, server_port, [
            {"given_name": "OK", "ip_address": "1.2.3.4", "mac_address": "aa:bb", "snr": XSS, "iface": "RF_5GHZ"},
        ])
        page.wait_for_selector(".client-card", state="attached")

        assert page.locator("img[src='x']").count() == 0
        badge_text = page.locator(".signal-badge").first.text_content()
        assert "0 dB" in badge_text


class TestDisplayMeshNodesXSS:
    def test_node_name_is_escaped(self, page: Page, server_port: int):
        # role: 3 → routed to meshNodes list by updateUI()
        _setup(page, server_port, [
            {"name": XSS, "ip_address": "10.0.0.1", "mac_address": "ff:ee:dd:cc:bb:aa", "snr": 25.0, "role": 3},
        ])
        page.wait_for_selector("#meshNodesTable td.label", state="attached")

        label_text = page.locator("#meshNodesTable td.label").first.text_content()
        assert XSS in label_text
        assert page.locator("img[src='x']").count() == 0

    def test_node_ip_is_escaped(self, page: Page, server_port: int):
        _setup(page, server_port, [
            {"name": "Safe Node", "ip_address": XSS, "mac_address": "ff:ee:dd:cc:bb:aa", "snr": 25.0, "role": 3},
        ])
        page.wait_for_selector("#meshNodesTable td.label", state="attached")

        value_text = page.locator("#meshNodesTable td.value").first.text_content()
        assert XSS in value_text
        assert page.locator("img[src='x']").count() == 0

    def test_node_mac_is_escaped(self, page: Page, server_port: int):
        _setup(page, server_port, [
            {"name": "Safe Node", "ip_address": "10.0.0.1", "mac_address": XSS, "snr": 25.0, "role": 3},
        ])
        page.wait_for_selector("#meshNodesTable td.label", state="attached")

        label_text = page.locator("#meshNodesTable td.label").first.text_content()
        assert XSS in label_text
        assert page.locator("img[src='x']").count() == 0

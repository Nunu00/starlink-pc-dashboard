import http.client
import threading
from http.server import HTTPServer

import pytest

from star_debug_server import DashboardHTTPHandler, StarlinkBridge


@pytest.fixture(scope="module")
def server_port():
    DashboardHTTPHandler.bridge = StarlinkBridge(use_mock=True)
    DashboardHTTPHandler.log_message = lambda *args: None  # silence request logs
    httpd = HTTPServer(("127.0.0.1", 0), DashboardHTTPHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()
    yield port
    httpd.shutdown()


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    return conn.getresponse()


class TestStaticFilePathTraversal:
    def test_traversal_one_level_up_returns_403(self, server_port):
        # /../star_debug_server.py → strips leading / → ../star_debug_server.py
        resp = get(server_port, "/../star_debug_server.py")
        assert resp.status == 403

    def test_traversal_deep_returns_403(self, server_port):
        resp = get(server_port, "/../../../../../../etc/passwd")
        assert resp.status == 403

    def test_traversal_to_sibling_directory_returns_403(self, server_port):
        resp = get(server_port, "/../tests/test_path_traversal.py")
        assert resp.status == 403

    def test_valid_missing_file_returns_404(self, server_port):
        resp = get(server_port, "/nonexistent.js")
        assert resp.status == 404

    def test_valid_index_returns_200(self, server_port):
        resp = get(server_port, "/index.html")
        assert resp.status == 200

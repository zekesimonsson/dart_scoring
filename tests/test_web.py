"""Tests for the scoreboard server's JSON API (dart_scoring.web)."""
# Test names describe the behaviour under test.
# pylint: disable=missing-function-docstring

import json
import threading
import urllib.error
import urllib.request

import pytest

from dart_scoring.game import Game
from dart_scoring.web import DartServer

CAPTURE = "20261113-213827-026-dart"


@pytest.fixture(name="server")
def fixture_server(tmp_path):
    (tmp_path / CAPTURE).mkdir()
    server = DartServer(("127.0.0.1", 0), Game(["A", "B"]), capture_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def post(server, path, body):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}",
                                     json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read())


def test_correcting_a_detected_dart_saves_the_truth(server):
    post(server, "/api/throw", {"x": 0.1, "y": -0.62, "capture": CAPTURE})  # T1
    state = post(server, "/api/correct", {"index": 0, "segment": 20, "multiplier": 1})
    assert state["turn"][0]["label"] == "20"
    assert state["turn"][0]["corrected"]
    assert state["players"][0]["remaining"] == 481
    truth = json.loads((server.capture_dir / CAPTURE / "truth.json").read_text())
    assert (truth["segment"], truth["multiplier"], truth["detected"]) == (20, 1, "T1")


def test_correcting_twice_keeps_what_the_detector_said(server):
    post(server, "/api/throw", {"x": 0.1, "y": -0.62, "capture": CAPTURE})
    post(server, "/api/correct", {"index": 0, "segment": 20, "multiplier": 3})
    post(server, "/api/correct", {"index": 0, "segment": 20, "multiplier": 1})
    truth = json.loads((server.capture_dir / CAPTURE / "truth.json").read_text())
    assert (truth["label"], truth["detected"]) == ("20", "T1")


def test_correcting_a_manual_dart_saves_nothing(server):
    post(server, "/api/throw", {"segment": 1, "multiplier": 3})
    state = post(server, "/api/correct", {"index": 0, "segment": 1, "multiplier": 1})
    assert state["turn"][0]["label"] == "1"
    assert not (server.capture_dir / CAPTURE / "truth.json").exists()


def test_a_capture_name_cannot_point_outside_the_capture_folder(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, "/api/throw", {"segment": 20, "multiplier": 1, "capture": "../../etc"})
    assert error.value.code == 400

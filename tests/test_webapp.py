"""
Tests for the personal training dashboard (garmin_mcp.webapp).

These tests never talk to the real Garmin or Anthropic APIs: the Garmin
client and the anthropic SDK are replaced with lightweight fakes so the
suite runs offline and without credentials.
"""

import sys
import types

import pytest
from fastapi.testclient import TestClient

from garmin_mcp import webapp


class FakeGarmin:
    def get_full_name(self):
        return "Karl Lander"

    def get_unit_system(self):
        return "metric"

    def get_stats(self, date):
        return {"totalSteps": 8123, "restingHeartRate": 52}

    def get_body_battery(self, start, end):
        return [{"charged": 80, "drained": 65}]

    def get_stress_data(self, date):
        return {"avgStressLevel": 24}

    def get_training_readiness(self, date):
        return [{"score": 71, "level": "MODERATE"}]

    def get_training_status(self, date):
        return {
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {"12345": {"trainingStatus": "PRODUCTIVE"}}
            }
        }

    def get_hrv_data(self, date):
        return {"hrvSummary": {"weeklyAvg": 45}}

    def get_rhr_day(self, date):
        return {"restingHeartRate": 52}

    def get_sleep_data(self, date):
        return {"dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 82}}}}

    def get_max_metrics(self, date):
        return [{"generic": {"vo2MaxPreciseValue": 52.3}, "fitnessAge": 29}]

    def get_endurance_score(self, start, end):
        return {"score": 55}

    def get_hill_score(self, start, end):
        return {"score": 40}

    def get_race_predictions(self):
        return {"time5K": 1234}

    def get_personal_record(self):
        return [{"typeId": 1, "value": 1234}]

    def get_goals(self, goal_type):
        return [{"goalTypeName": "Marathon"}]

    def get_activities(self, start, limit):
        return [
            {
                "startTimeLocal": "2026-09-13T07:00:00",
                "activityType": {"typeKey": "running"},
                "activityName": "Morning Run",
                "distance": 10000,
                "duration": 3000,
                "averageHR": 145,
                "calories": 650,
            }
        ]

    def get_weigh_ins(self, start, end):
        return {"dailyWeightSummaries": [{"summaryDate": "2026-09-10", "allWeightMetrics": [{"weight": 75000}]}]}

    def get_devices(self):
        return [{"productDisplayName": "Forerunner 965"}]

    def get_device_last_used(self):
        return {"userProfileNumber": 4242}

    def get_gear(self, profile_id):
        assert profile_id == 4242
        return [{"customMakeModel": "Nike Pegasus"}]


@pytest.fixture
def dashboard(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "karl")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret123")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    webapp.garmin_client = FakeGarmin()
    webapp._overview_cache["data"] = None
    webapp._overview_cache["at"] = None
    return TestClient(webapp.create_app())


def test_dashboard_requires_login(dashboard):
    assert dashboard.get("/").status_code == 401
    assert dashboard.get("/", auth=("karl", "wrong")).status_code == 401


def test_dashboard_serves_page_with_correct_credentials(dashboard):
    resp = dashboard.get("/", auth=("karl", "secret123"))
    assert resp.status_code == 200
    assert "Trainingscockpit" in resp.text


def test_overview_aggregates_garmin_data(dashboard):
    resp = dashboard.get("/api/overview", auth=("karl", "secret123"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile"]["full_name"]["data"] == "Karl Lander"
    assert data["recent_activities"]["data"][0]["activityName"] == "Morning Run"
    assert data["gear"]["data"][0]["customMakeModel"] == "Nike Pegasus"


def test_overview_survives_partial_garmin_failures(dashboard, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("Garmin API down")

    monkeypatch.setattr(webapp.garmin_client, "get_sleep_data", boom)
    resp = dashboard.get("/api/overview", auth=("karl", "secret123"))
    assert resp.status_code == 200
    assert resp.json()["sleep_last_night"]["ok"] is False


def test_advisor_requires_anthropic_key(dashboard):
    resp = dashboard.post(
        "/api/advisor",
        json={"message": "Wie sollte meine Woche aussehen?"},
        auth=("karl", "secret123"),
    )
    assert resp.status_code == 501


def test_advisor_uses_live_training_context(dashboard, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    captured = {}

    class FakeTextBlock:
        type = "text"

        def __init__(self, text):
            self.text = text

    class FakeMessages:
        def create(self, model, max_tokens, system, messages):
            captured["system"] = system
            captured["messages"] = messages
            return types.SimpleNamespace(content=[FakeTextBlock("Trainiere heute locker.")])

    class FakeAnthropic:
        def __init__(self, api_key):
            captured["api_key"] = api_key
            self.messages = FakeMessages()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    resp = dashboard.post(
        "/api/advisor",
        json={"message": "Wie sollte meine Woche aussehen?"},
        auth=("karl", "secret123"),
    )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Trainiere heute locker."
    assert captured["api_key"] == "fake-key-for-test"
    assert "Karl Lander" in captured["system"]
    assert "PRODUCTIVE" in captured["system"]
    assert captured["messages"][-1] == {"role": "user", "content": "Wie sollte meine Woche aussehen?"}

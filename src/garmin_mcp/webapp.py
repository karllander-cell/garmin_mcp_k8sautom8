"""
Persönliches Trainings-Dashboard mit KI-Trainingsberater.

Ein eigenständiger, passwortgeschützter Webservice, der auf denselben Garmin
Connect-Zugang wie der MCP-Server zugreift. Er zeigt alle verfügbaren
Trainings- und Gesundheitsdaten übersichtlich an und lässt dich per Chat
mit Claude über deinen Trainingsplan sprechen - Claude bekommt dabei deine
aktuellen Daten als Kontext mitgeliefert.

Bewusst als separater Prozess/Port vom MCP-Server gehalten, damit ein Fehler
oder Neustart des Dashboards den produktiven MCP-Server nicht beeinflusst.

Sicherheit: Der Zugriff ist per HTTP Basic Auth auf ein einzelnes Konto
beschränkt (DASHBOARD_USERNAME / DASHBOARD_PASSWORD). Das ersetzt kein TLS -
in Kubernetes/Docker sollte der Service nur über eine Ingress/Gateway-Route
mit HTTPS erreichbar sein und nicht direkt öffentlich exponiert werden.
"""

import datetime
import os
import secrets
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from garmin_mcp import init_api

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

garmin_client = None
_overview_cache: dict[str, Any] = {"data": None, "at": None}
OVERVIEW_CACHE_SECONDS = int(os.environ.get("DASHBOARD_CACHE_SECONDS", "300"))

security = HTTPBasic()


def require_login(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Single-user HTTP Basic Auth gate - only the configured account may in."""
    expected_user = os.environ.get("DASHBOARD_USERNAME", "")
    expected_pass = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected_user or not expected_pass:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DASHBOARD_USERNAME/DASHBOARD_PASSWORD sind auf dem Server nicht gesetzt.",
        )
    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pass_ok = secrets.compare_digest(credentials.password, expected_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Ungueltige Zugangsdaten",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _safe(fn, *args, **kwargs):
    """Call a Garmin API function and never let one failing metric break the page."""
    try:
        result = fn(*args, **kwargs)
        return {"ok": True, "data": result}
    except Exception as exc:  # noqa: BLE001 - deliberately broad, this is a best-effort dashboard
        return {"ok": False, "error": str(exc)}


def _iso(d: datetime.date) -> str:
    return d.strftime("%Y-%m-%d")


def _build_overview() -> dict[str, Any]:
    if garmin_client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Garmin-Client ist nicht initialisiert.")

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    week_ago = today - datetime.timedelta(days=7)
    month_ago = today - datetime.timedelta(days=30)

    profile = {
        "full_name": _safe(garmin_client.get_full_name),
        "unit_system": _safe(garmin_client.get_unit_system),
    }

    today_block = {
        "date": _iso(today),
        "stats": _safe(garmin_client.get_stats, _iso(today)),
        "body_battery": _safe(garmin_client.get_body_battery, _iso(today), _iso(today)),
        "stress": _safe(garmin_client.get_stress_data, _iso(today)),
        "training_readiness": _safe(garmin_client.get_training_readiness, _iso(today)),
        "training_status": _safe(garmin_client.get_training_status, _iso(today)),
        "hrv": _safe(garmin_client.get_hrv_data, _iso(today)),
        "resting_heart_rate": _safe(garmin_client.get_rhr_day, _iso(today)),
    }

    sleep_last_night = _safe(garmin_client.get_sleep_data, _iso(yesterday))

    fitness = {
        "max_metrics": _safe(garmin_client.get_max_metrics, _iso(today)),
        "endurance_score": _safe(garmin_client.get_endurance_score, _iso(week_ago), _iso(today)),
        "hill_score": _safe(garmin_client.get_hill_score, _iso(week_ago), _iso(today)),
        "race_predictions": _safe(garmin_client.get_race_predictions),
        "personal_records": _safe(garmin_client.get_personal_record),
    }

    goals = _safe(garmin_client.get_goals, "active")

    activities = _safe(garmin_client.get_activities, 0, 15)

    weight = _safe(garmin_client.get_weigh_ins, _iso(month_ago), _iso(today))

    devices_result = _safe(garmin_client.get_devices)
    last_used_device = _safe(garmin_client.get_device_last_used)
    gear = {"ok": False, "error": "Kein Nutzerprofil gefunden, um Ausruestung zuzuordnen."}
    if last_used_device["ok"] and last_used_device["data"]:
        device_info = last_used_device["data"]
        profile_id = (
            device_info.get("userProfileNumber")
            or device_info.get("userProfilePk")
            or device_info.get("userProfileId")
        )
        if profile_id:
            gear = _safe(garmin_client.get_gear, profile_id)

    return {
        "generated_at": datetime.datetime.now().isoformat(),
        "profile": profile,
        "today": today_block,
        "sleep_last_night": sleep_last_night,
        "fitness": fitness,
        "active_goals": goals,
        "recent_activities": activities,
        "weight_trend": weight,
        "devices": devices_result,
        "gear": gear,
    }


def get_overview(force_refresh: bool = False) -> dict[str, Any]:
    now = datetime.datetime.now()
    cached_at = _overview_cache["at"]
    if (
        not force_refresh
        and cached_at is not None
        and (now - cached_at).total_seconds() < OVERVIEW_CACHE_SECONDS
        and _overview_cache["data"] is not None
    ):
        return _overview_cache["data"]

    data = _build_overview()
    _overview_cache["data"] = data
    _overview_cache["at"] = now
    return data


def _extract(block: Optional[dict], *path, default="-"):
    """Best-effort digging into a _safe() result without ever raising."""
    if not block or not block.get("ok"):
        return default
    node = block["data"]
    for key in path:
        while isinstance(node, list):
            node = node[0] if node else None
        if node is None:
            return default
        if isinstance(node, dict):
            node = node.get(key)
        else:
            return default
    while isinstance(node, list):
        node = node[0] if node else None
    return node if node is not None else default


def _training_status_value(block: Optional[dict], default="-"):
    """Garmin nests the latest training status under a dynamic device-id key."""
    if not block or not block.get("ok"):
        return default
    try:
        data = block["data"]
        if isinstance(data, list):
            data = data[0] if data else {}
        latest = (data or {}).get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})
        if latest:
            first_key = next(iter(latest))
            return latest[first_key].get("trainingStatus", default)
        return (data or {}).get("trainingStatus", default)
    except Exception:  # noqa: BLE001
        return default


def build_advisor_context(overview: dict[str, Any]) -> str:
    """Compact, human-readable summary of the current data for the LLM prompt."""
    lines: list[str] = []

    name = _extract(overview["profile"]["full_name"], default=None)
    lines.append(f"Athlet: {name or 'unbekannt'}")

    today = overview["today"]
    lines.append(f"Datum: {today['date']}")
    lines.append(f"Ruheherzfrequenz heute: {_extract(today['resting_heart_rate'], 'restingHeartRate')}")
    lines.append(
        f"Trainingsbereitschaft (Training Readiness): {_extract(today['training_readiness'], 'score')} "
        f"({_extract(today['training_readiness'], 'level')})"
    )
    lines.append(f"Trainingsstatus: {_training_status_value(today['training_status'])}")
    lines.append(
        f"Body Battery: geladen {_extract(today['body_battery'], 'charged')} / "
        f"verbraucht {_extract(today['body_battery'], 'drained')}"
    )
    lines.append(f"HRV: {_extract(today['hrv'], 'hrvSummary', 'weeklyAvg')}")

    lines.append(f"Schlaf letzte Nacht (Rohdaten-Auszug): {_extract(overview['sleep_last_night'], 'dailySleepDTO', 'sleepTimeSeconds')} Sekunden Schlafzeit")

    fitness = overview["fitness"]
    lines.append(f"VO2max/Fitnessalter: {_extract(fitness['max_metrics'])}")
    lines.append(f"Wettkampfvorhersagen: {_extract(fitness['race_predictions'])}")
    lines.append(f"Persoenliche Rekorde: {_extract(fitness['personal_records'])}")

    lines.append(f"Aktive Ziele: {_extract(overview['active_goals'])}")
    lines.append(f"Gewichtsverlauf (30 Tage): {_extract(overview['weight_trend'])}")

    activities = overview["recent_activities"]
    lines.append("Letzte Aktivitaeten:")
    if activities.get("ok") and activities.get("data"):
        for act in activities["data"][:10]:
            lines.append(
                "  - {date} | {typ} | {name} | {dist} m | {dur} s | avgHR {hr}".format(
                    date=act.get("startTimeLocal", "?"),
                    typ=(act.get("activityType") or {}).get("typeKey", "?"),
                    name=act.get("activityName", "?"),
                    dist=act.get("distance", "?"),
                    dur=act.get("duration", "?"),
                    hr=act.get("averageHR", "?"),
                )
            )
    else:
        lines.append(f"  (nicht verfuegbar: {activities.get('error', 'unbekannt')})")

    return "\n".join(lines)


ADVISOR_SYSTEM_PROMPT_TEMPLATE = """Du bist ein erfahrener, persoenlicher Ausdauer- und Fitness-Coach.
Du berätst den Athleten ausschliesslich auf Basis seiner echten Garmin-Connect-Daten,
die dir unten als aktueller Kontext mitgegeben werden. Sprich Deutsch, sei konkret,
motivierend und ehrlich. Wenn Daten fuer eine Einschaetzung fehlen, sag das offen,
statt zu raten. Gib, wo sinnvoll, konkrete naechste Trainingsschritte
(Belastung, Intensitaet, Regeneration) statt nur allgemeiner Tipps.

AKTUELLE TRAININGS- UND GESUNDHEITSDATEN:
{context}
"""


class ChatMessage(BaseModel):
    role: str
    content: str


class AdvisorRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class AdvisorResponse(BaseModel):
    reply: str


def create_app() -> FastAPI:
    app = FastAPI(title="Mein Trainingscockpit", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "garmin-dashboard"}

    @app.get("/")
    def index(_user: str = Depends(require_login)):
        return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))

    @app.get("/api/overview")
    def api_overview(refresh: bool = False, _user: str = Depends(require_login)):
        try:
            return JSONResponse(get_overview(force_refresh=refresh))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Konnte Trainingsdaten nicht laden: {exc}")

    @app.post("/api/advisor", response_model=AdvisorResponse)
    def api_advisor(req: AdvisorRequest, _user: str = Depends(require_login)):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED,
                "ANTHROPIC_API_KEY ist nicht gesetzt - der Trainingsberater ist nicht verfuegbar.",
            )

        try:
            import anthropic
        except ImportError as exc:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, f"anthropic-Paket fehlt: {exc}")

        try:
            overview = get_overview()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Konnte Trainingsdaten nicht laden: {exc}")

        context = build_advisor_context(overview)
        system_prompt = ADVISOR_SYSTEM_PROMPT_TEMPLATE.format(context=context)

        model = os.environ.get("ADVISOR_MODEL", "claude-sonnet-5")
        messages = [{"role": m.role, "content": m.content} for m in req.history]
        messages.append({"role": "user", "content": req.message})

        client = anthropic.Anthropic(api_key=api_key)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1200,
                system=system_prompt,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Anfrage an Claude fehlgeschlagen: {exc}")

        reply = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        return AdvisorResponse(reply=reply or "(keine Antwort erhalten)")

    return app


def main():
    """Initialise the Garmin client and start the dashboard web server."""
    global garmin_client

    from dotenv import load_dotenv

    load_dotenv()

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")

    if not os.environ.get("DASHBOARD_USERNAME") or not os.environ.get("DASHBOARD_PASSWORD"):
        raise SystemExit(
            "DASHBOARD_USERNAME und DASHBOARD_PASSWORD muessen gesetzt sein, "
            "damit das Dashboard nur fuer dich zugreifbar ist."
        )

    garmin_client = init_api(email, password)
    if not garmin_client:
        raise SystemExit("Garmin Connect Login fehlgeschlagen - Dashboard wird nicht gestartet.")

    print("Garmin Connect Client fuer das Dashboard initialisiert.")

    app = create_app()

    import uvicorn

    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    print(f"Starte Trainings-Dashboard auf {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

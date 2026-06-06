from flask import Flask, jsonify, render_template, request
import requests
import os
import time
import threading

app = Flask(__name__)

MAIN_PI_URL = os.environ.get("GWL_MAIN_PI_URL")

REQUEST_TIMEOUT = 3

# -----------------------------
# Public API Cache
# -----------------------------
STATUS_CACHE_TTL = 1.0          # seconds
DASHBOARD_CACHE_TTL = 5.0       # seconds
ZONES_CACHE_TTL = 60.0          # seconds

status_cache = {}
dashboard_cache = {
    "time": 0,
    "data": None,
    "status": 200
}
zones_cache = {
    "time": 0,
    "data": None,
    "status": 200
}

cache_lock = threading.Lock()

# -----------------------------
# Public Visitor Tracking
# -----------------------------
VISITOR_ACTIVE_WINDOW = 15.0  # seconds

visitor_sessions = {}
visitor_lock = threading.Lock()

def load_public_allowed_zones():
    raw = os.environ.get("PUBLIC_ALLOWED_ZONES", "")

    zones = {
        zone.strip()
        for zone in raw.split(",")
        if zone.strip()
    }

    return zones

PUBLIC_ALLOWED_ZONES = load_public_allowed_zones()

def is_zone_public(zone_name):
    return zone_name in PUBLIC_ALLOWED_ZONES

def build_public_summary_from_status(status_data):
    """
    Build public dashboard summary using full /api/status data from main Pi.

    Summary:
    1. fully_synced_zones / total_zones
    2. online_junctions / total_junctions

    Extra hover details:
    1. desync_zones
    2. offline_junctions_list
    """

    zone_counts = {}
    offline_junctions_list = []

    all_junctions = status_data.get("data", {})

    for ip, state in all_junctions.items():
        zone_name = state.get("zone")

        if zone_name not in PUBLIC_ALLOWED_ZONES:
            continue

        if zone_name not in zone_counts:
            zone_counts[zone_name] = {
                "total": 0,
                "sync": 0,
                "offline": 0
            }

        zone_counts[zone_name]["total"] += 1

        if (
            state.get("connected")
            and state.get("sync_status") is True
        ):
            zone_counts[zone_name]["sync"] += 1

        is_offline = (
            not state.get("connected")
            or state.get("sync_status") == "OFFLINE"
        )

        if is_offline:
            zone_counts[zone_name]["offline"] += 1

            offline_junctions_list.append({
                "zone": zone_name,
                "name": state.get("name") or ip,
                "ip": ip
            })

    total_zones = len(zone_counts)

    fully_synced_zones = sum(
        1
        for counts in zone_counts.values()
        if counts["total"] > 0 and counts["sync"] == counts["total"]
    )

    desync_zones = []

    for zone_name, counts in zone_counts.items():
        if counts["total"] > 0 and counts["sync"] < counts["total"]:
            desync_zones.append({
                "zone": zone_name,
                "sync": counts["sync"],
                "total": counts["total"],
                "offline": counts["offline"]
            })

    total_junctions = sum(
        counts["total"]
        for counts in zone_counts.values()
    )

    offline_junctions = sum(
        counts["offline"]
        for counts in zone_counts.values()
    )

    online_junctions = total_junctions - offline_junctions

    return {
        "total_zones": total_zones,
        "fully_synced_zones": fully_synced_zones,
        "total_junctions": total_junctions,
        "online_junctions": online_junctions,
        "offline_junctions": offline_junctions,
        "desync_zones": desync_zones,
        "offline_junctions_list": offline_junctions_list
    }

def filter_dashboard_status(data, status_data=None):
    allowed_zones = PUBLIC_ALLOWED_ZONES

    filtered_zones = [
        item for item in data.get("zones", [])
        if item.get("zone") in allowed_zones
    ]

    filtered_pending_zones = [
        item for item in data.get("pending_zones", [])
        if item.get("zone") in allowed_zones
    ]

    data["zones"] = filtered_zones
    data["pending_zones"] = filtered_pending_zones

    if isinstance(status_data, dict):
        summary = build_public_summary_from_status(status_data)

        data["total_zones"] = summary["total_zones"]
        data["fully_synced_zones"] = summary["fully_synced_zones"]
        data["total_junctions"] = summary["total_junctions"]
        data["online_junctions"] = summary["online_junctions"]
        data["offline_junctions"] = summary["offline_junctions"]
        data["desync_zones"] = summary["desync_zones"]
        data["offline_junctions_list"] = summary["offline_junctions_list"]

    else:
        data["total_zones"] = 0
        data["fully_synced_zones"] = 0
        data["total_junctions"] = 0
        data["online_junctions"] = 0
        data["offline_junctions"] = 0
        data["desync_zones"] = []
        data["offline_junctions_list"] = []

    data["all_offline"] = (
        data["total_junctions"] > 0
        and data["offline_junctions"] == data["total_junctions"]
    )

    return data


def require_main_pi_url():
    if not MAIN_PI_URL:
        return False
    return True


def fetch_from_main_pi(path, params=None):
    if not require_main_pi_url():
        return {
            "error": "GWL_MAIN_PI_URL is not set on public UI Raspberry Pi."
        }, 500

    try:
        response = requests.get(
            f"{MAIN_PI_URL}{path}",
            params=params,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json(), 200

    except requests.exceptions.Timeout:
        return {
            "error": "Timeout while connecting to main GWL Raspberry Pi."
        }, 504

    except requests.exceptions.ConnectionError:
        return {
            "error": "Cannot connect to main GWL Raspberry Pi."
        }, 502

    except requests.exceptions.HTTPError as e:
        return {
            "error": f"Main GWL Raspberry Pi returned HTTP error: {e}"
        }, 502

    except Exception as e:
        return {
            "error": str(e)
        }, 500

def record_visitor():
    visitor_id = request.args.get("visitor_id")

    if not visitor_id:
        return

    now = time.time()

    with visitor_lock:
        visitor_sessions[visitor_id] = now

        expired_ids = [
            sid for sid, last_seen in visitor_sessions.items()
            if now - last_seen > VISITOR_ACTIVE_WINDOW
        ]

        for sid in expired_ids:
            visitor_sessions.pop(sid, None)

def get_active_visitor_count():
    now = time.time()

    with visitor_lock:
        expired_ids = [
            sid for sid, last_seen in visitor_sessions.items()
            if now - last_seen > VISITOR_ACTIVE_WINDOW
        ]

        for sid in expired_ids:
            visitor_sessions.pop(sid, None)

        return len(visitor_sessions)

@app.route("/")
def public_dashboard():
    return render_template("public_dashboard.html")

@app.route("/monitor")
def monitor():
    return render_template("public_monitor.html")

# -----------------------------
# Read-only proxy APIs
# -----------------------------

@app.route("/api/zones")
def public_api_zones():
    now = time.time()

    with cache_lock:
        if (
            zones_cache["data"] is not None
            and now - zones_cache["time"] < ZONES_CACHE_TTL
        ):
            return jsonify(zones_cache["data"]), zones_cache["status"]

    data, status = fetch_from_main_pi("/api/zones")

    if status != 200:
        return jsonify(data), status

    if not isinstance(data, list):
        return jsonify(data), status

    filtered_zones = [
        zone for zone in data
        if zone in PUBLIC_ALLOWED_ZONES
    ]

    with cache_lock:
        zones_cache["time"] = now
        zones_cache["data"] = filtered_zones
        zones_cache["status"] = status

    return jsonify(filtered_zones), status


@app.route("/api/dashboard_status")
def public_api_dashboard_status():
    now = time.time()

    with cache_lock:
        if (
            dashboard_cache["data"] is not None
            and now - dashboard_cache["time"] < DASHBOARD_CACHE_TTL
        ):
            return jsonify(dashboard_cache["data"]), dashboard_cache["status"]

    data, status = fetch_from_main_pi("/api/dashboard_status")

    if status != 200:
        return jsonify(data), status

    status_data, status_data_code = fetch_from_main_pi("/api/status")

    if isinstance(data, dict):
        if status_data_code == 200 and isinstance(status_data, dict):
            data = filter_dashboard_status(data, status_data)
        else:
            data = filter_dashboard_status(data)

    with cache_lock:
        dashboard_cache["time"] = now
        dashboard_cache["data"] = data
        dashboard_cache["status"] = status

    return jsonify(data), status

@app.route("/api/get_display_schedule")
def public_api_get_display_schedule():
    zone = request.args.get("zone")

    if not zone:
        return jsonify({
            "error": "Zone is required."
        }), 400

    if not is_zone_public(zone):
        return jsonify({
            "error": "This zone is not available for public viewing."
        }), 403

    data, status = fetch_from_main_pi(
        "/api/get_display_schedule",
        params={
            "zone": zone
        }
    )

    return jsonify(data), status

@app.route("/api/status")
def public_api_status():
    record_visitor()

    zone = request.args.get("zone")

    if not zone:
        return jsonify({
            "error": "Zone is required."
        }), 400

    if not is_zone_public(zone):
        return jsonify({
            "error": "This zone is not available for public viewing."
        }), 403

    now = time.time()
    cache_key = zone

    # Return cached status if still fresh.
    with cache_lock:
        cached = status_cache.get(cache_key)

        if cached and now - cached["time"] < STATUS_CACHE_TTL:
            #print("CACHE HIT:", zone)
            return jsonify(cached["data"]), cached["status"]

    # IMPORTANT:
    # Do not forward "since" for public cache.
    # The cache must store full zone data, not partial updates.
    # print("CACHE MISS:", zone)
    data, status = fetch_from_main_pi(
        "/api/status",
        params={
            "zone": zone
        }
    )

    # Cache successful response only.
    # If main has error, return error but do not overwrite good cache.
    if status == 200:
        with cache_lock:
            status_cache[cache_key] = {
                "time": now,
                "data": data,
                "status": status
            }

    return jsonify(data), status

@app.route("/api/visitor_count")
def public_api_visitor_count():
    return jsonify({
        "active_visitors": get_active_visitor_count(),
        "active_window_seconds": VISITOR_ACTIVE_WINDOW
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
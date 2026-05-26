from flask import Flask, jsonify, render_template, request
import requests
import os
import time
import threading

app = Flask(__name__)

# First Raspberry Pi backend.
# Set this using environment variable:
# export GWL_MAIN_PI_URL="http://FIRST_PI_IP:5000"
MAIN_PI_URL = os.environ.get("GWL_MAIN_PI_URL")

REQUEST_TIMEOUT = 3

# -----------------------------
# Public API Cache
# -----------------------------
STATUS_CACHE_TTL = 1.0          # seconds
DASHBOARD_CACHE_TTL = 5.0       # seconds
ZONES_CACHE_TTL = 30.0          # seconds

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

# -----------------------------
# Public Zone Visibility
# -----------------------------
# Only zones listed here will be visible on the public UI.
# Use exact zone names from the first Pi.
PUBLIC_ALLOWED_ZONES = {
    "Kulim Hi Tech",
    "Kulim",
    "Muar",
    "Kuala Selangor",
    "Kuala Langat",
    "MBSJ",
    "Port Dickson",
    "Bentong",
    "Kota Laksamana",
    "Ayer Keroh",
    "Pekan",
    "Gombak",
    "Kluang",
    "Perak1_Aeon",
    "Perak2_tolpulai",
    "Perak3_rokam",
    "Kelantan1_STL2024",
    "Kelantan1_STL2025",
    "Kelantan1_STL2025_2",
    "JKR Terengganu",
    "MBIP Pontian Link",
    "Kota Tinggi",
    "MBKT",
    "Bukit Beruang",
    "NTC Kulim",
    "Ayer Keroh Gong",
    "AMJ1",
    "AMJ2 Melaka Sentral",
    "Balai Polis Bandar Hilir",
    "Lebuh SPA"

}

def is_zone_public(zone_name):
    return zone_name in PUBLIC_ALLOWED_ZONES


def filter_dashboard_status(data):
    allowed_zones = PUBLIC_ALLOWED_ZONES

    filtered_zones = [
        item for item in data.get("zones", [])
        if item.get("zone") in allowed_zones
    ]

    filtered_pending_zones = [
        item for item in data.get("pending_zones", [])
        if item.get("zone") in allowed_zones
    ]

    visible_total_junctions = sum(
        item.get("total", 0)
        for item in filtered_zones + filtered_pending_zones
    )

    visible_offline_junctions = sum(
        item.get("offline", 0)
        for item in filtered_zones + filtered_pending_zones
    )

    data["zones"] = filtered_zones
    data["pending_zones"] = filtered_pending_zones
    data["total_junctions"] = visible_total_junctions
    data["offline_junctions"] = visible_offline_junctions

    data["all_offline"] = (
        visible_total_junctions > 0
        and visible_offline_junctions == visible_total_junctions
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

    if isinstance(data, dict):
        data = filter_dashboard_status(data)

    with cache_lock:
        dashboard_cache["time"] = now
        dashboard_cache["data"] = data
        dashboard_cache["status"] = status

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
    #print("CACHE MISS:", zone)
    data, status = fetch_from_main_pi(
        "/api/status",
        params={
            "zone": zone
        }
    )

    # Cache successful response only.
    # If main Pi has error, return error but do not overwrite good cache.
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
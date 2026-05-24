from flask import Flask, jsonify, render_template, request
import requests
import os

app = Flask(__name__)

# First Raspberry Pi backend.
# Set this using environment variable:
# export GWL_MAIN_PI_URL="http://FIRST_PI_IP:5000"
MAIN_PI_URL = os.environ.get("GWL_MAIN_PI_URL")

REQUEST_TIMEOUT = 3


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
    data, status = fetch_from_main_pi("/api/zones")
    return jsonify(data), status


@app.route("/api/dashboard_status")
def public_api_dashboard_status():
    data, status = fetch_from_main_pi("/api/dashboard_status")
    return jsonify(data), status


@app.route("/api/status")
def public_api_status():
    zone = request.args.get("zone")
    since = request.args.get("since")

    params = {}

    if zone:
        params["zone"] = zone

    if since:
        params["since"] = since

    data, status = fetch_from_main_pi("/api/status", params=params)
    return jsonify(data), status


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
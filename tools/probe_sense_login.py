"""Check whether SE-Monitor credentials still authenticate against the legacy Sense API.

Reads SE_USERNAME / SE_PASSWORD from ../.env. Prints only the HTTP status and the
top-level JSON keys / monitor info; tokens are never printed.
"""
import json, pathlib, urllib.parse, urllib.request, urllib.error

env = {}
for line in (pathlib.Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

data = urllib.parse.urlencode({"email": env["SE_USERNAME"], "password": env["SE_PASSWORD"]}).encode()
req = urllib.request.Request(
    "https://api.sense.com/apiservice/api/v1/authenticate",
    data=data,
    headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "schneider-se-monitor-probe"},
)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
        print("HTTP", r.status)
        print("keys:", sorted(body))
        for m in body.get("monitors", []):
            print("monitor:", {k: m.get(k) for k in ("id", "serial_number", "hardware_type", "solar_configured", "online")})
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:300].decode(errors="replace"))

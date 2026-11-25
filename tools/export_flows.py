"""Export Schneider-related flows to captures/flows_redacted.txt with secrets masked.
Usage: mitmdump -nr captures/se_monitor.flows -s tools/export_flows.py -q
"""
import json, pathlib, re
from mitmproxy import http

OUT = pathlib.Path(__file__).resolve().parent.parent / "captures" / "flows_redacted.txt"
HOSTS = ("clipsalcortex", "cognito", "se.com", "se.app")
SECRET_KEYS = re.compile(r"(token|password|secret|code|session|cookie|authorization|assertion|SRP_A|SRP_B|PASSWORD_CLAIM|SALT|SECRET_BLOCK|SIGNATURE|state|nonce|verifier|challenge)", re.I)
OUT.write_text("")

def mask(v):
    s = str(v)
    return f"<redacted len={len(s)}>"

TOKENLIKE = re.compile(r"^(eyJ[\w-]+\.[\w-]+\.[\w-]*|v1\.[\w-]{20,}|[A-Za-z0-9_\-+/=.]{60,})$")

def scrub(obj):
    if isinstance(obj, str) and TOKENLIKE.match(obj):
        return mask(obj)
    if isinstance(obj, dict) and set(obj) >= {"Name", "Value"} and SECRET_KEYS.search(str(obj["Name"])):
        return {"Name": obj["Name"], "Value": mask(obj["Value"])}
    if isinstance(obj, dict):
        return {k: (mask(v) if SECRET_KEYS.search(k) and not isinstance(v, (dict, list)) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(x) for x in obj[:5]] + ([f"... {len(obj)-5} more"] if len(obj) > 5 else [])
    return obj

def body(content, ctype):
    if not content:
        return ""
    if "json" in ctype:
        try:
            return json.dumps(scrub(json.loads(content)), indent=1)[:6000]
        except Exception:
            pass
    if "x-www-form-urlencoded" in ctype:
        from urllib.parse import parse_qsl
        return json.dumps(scrub(dict(parse_qsl(content.decode(errors="replace")))), indent=1)
    return f"<{len(content)} bytes {ctype}>"

def response(flow: http.HTTPFlow):
    r = flow.request
    if r.method == "OPTIONS" or not any(h in r.host for h in HOSTS):
        return
    from urllib.parse import urlsplit, parse_qsl
    u = urlsplit(r.pretty_url)
    q = scrub(dict(parse_qsl(u.query)))
    hdrs = {k: (mask(v) if SECRET_KEYS.search(k) else v) for k, v in r.headers.items() if k.lower() not in ("accept-language", "accept-encoding", "connection", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest")}
    loc = flow.response.headers.get("location", "")
    if loc:
        lu = urlsplit(loc)
        loc = f"{lu.scheme}://{lu.netloc}{lu.path} params={sorted(dict(parse_qsl(lu.query)))}"
    with OUT.open("a") as f:
        f.write(f"\n===== {r.method} {u.scheme}://{u.netloc}{u.path}\nquery: {json.dumps(q)}\nreq headers: {json.dumps(hdrs)}\n")
        f.write(f"req body: {body(r.content, r.headers.get('content-type',''))}\n")
        f.write(f"--> {flow.response.status_code} location: {loc}\nresp body: {body(flow.response.content, flow.response.headers.get('content-type',''))}\n")

def websocket_message(flow):
    if "clipsalcortex" not in flow.request.host:
        return
    m = flow.websocket.messages[-1]
    with OUT.open("a") as f:
        txt = m.content[:1500].decode(errors="replace")
        try:
            txt = json.dumps(scrub(json.loads(m.content)))[:1500]
        except Exception:
            pass
        f.write(f"WS {'client->' if m.from_client else '<-server'}: {txt}\n")

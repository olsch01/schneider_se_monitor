"""One-time browser login for testing. You complete the Schneider login (incl. captcha/MFA)
in your own browser; this script catches the redirect on http://localhost:8765, exchanges the
code with Cognito (PKCE) and saves tokens to ../.tokens.json (git-ignored). Tokens are not printed.
"""
import base64, hashlib, http.server, json, pathlib, secrets, time, urllib.parse, urllib.request, webbrowser

CLIENT_ID = "3o2v8tokrthvv2uk0mj3u0sgva"
DOMAIN = "https://clippy-prod.auth.us-east-1.amazoncognito.com"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}"
OUT = pathlib.Path(__file__).resolve().parent.parent / ".tokens.json"

verifier = secrets.token_urlsafe(64)[:128]
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state = secrets.token_urlsafe(16)
url = f"{DOMAIN}/oauth2/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
    "identity_provider": "EIM", "scope": "openid profile email aws.cognito.signin.user.admin",
    "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})

result = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        if "code" in q or "error" in q:
            result.update(q)
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
        self.wfile.write(b"Login captured. You can close this tab.")
    def log_message(self, *a): pass

print("Opening browser for Schneider login. If it doesn't open, visit:\n", url)
webbrowser.open(url)
srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
while not result:
    srv.handle_request()
if "error" in result or result.get("state") != state:
    raise SystemExit(f"Login failed: {result.get('error')} {result.get('error_description','')}")

data = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": CLIENT_ID,
    "code": result["code"], "redirect_uri": REDIRECT, "code_verifier": verifier}).encode()
req = urllib.request.Request(f"{DOMAIN}/oauth2/token", data=data,
    headers={"Content-Type": "application/x-www-form-urlencoded"})
tok = json.loads(urllib.request.urlopen(req, timeout=20).read())
tok["obtained_at"] = int(time.time())
OUT.write_text(json.dumps(tok)); OUT.chmod(0o600)
print("Saved tokens to", OUT, "| keys:", sorted(tok), "| expires_in:", tok.get("expires_in"))

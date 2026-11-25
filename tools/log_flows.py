"""mitmproxy addon: append a one-line summary of each request to captures/requests.log."""
import pathlib
from mitmproxy import http

LOG = pathlib.Path(__file__).resolve().parent.parent / "captures" / "requests.log"

def response(flow: http.HTTPFlow) -> None:
    with LOG.open("a") as f:
        f.write(f"{flow.response.status_code} {flow.request.method} {flow.request.pretty_url.split('?')[0]} "
                f"{flow.response.headers.get('content-type','')}\n")

def tls_failed_client(data) -> None:
    with LOG.open("a") as f:
        f.write(f"TLS-FAIL (pinning?) {data.context.server.address}\n")

import base64, json, mimetypes, sys, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
HARNESS = ROOT / "tests" / "offline-browser-harness.html"
KEY = ec.generate_private_key(ec.SECP256R1())
def b64(value): return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
    def send_bytes(self, content, kind="application/json", status=200):
        self.send_response(status); self.send_header("Content-Type", kind); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(content)
    def do_POST(self):
        if self.path != "/offline/provision": return self.send_bytes(b"{}", status=404)
        size=int(self.headers.get("Content-Length","0")); device=json.loads(self.rfile.read(size))["device_id"]; now=int(time.time())
        payload={"schema_version":2,"device_id":device,"user_id":10,"business_id":1,"auth_version":1,"role":"admin","permissions":{"sales.create":True},"issued_at":now,"last_server_verified":now,"expires_at":now+604800,"unlock_required":True}
        encoded=b64(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()); r,s=decode_dss_signature(KEY.sign(encoded.encode(),ec.ECDSA(hashes.SHA256())))
        numbers=KEY.public_key().public_numbers(); body={"payload":encoded,"signature":b64(r.to_bytes(32,"big")+s.to_bytes(32,"big")),"public_key":{"kty":"EC","crv":"P-256","x":b64(numbers.x.to_bytes(32,"big")),"y":b64(numbers.y.to_bytes(32,"big")),"ext":True}}
        self.send_bytes(json.dumps(body).encode())
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if self.path == "/offline/snapshot":
            snapshot={"schema_version":2,"verified_at":"2026-09-08T10:00:00Z","user":{"id":10,"business_id":1,"username":"admin","firstname":"Ada","lastname":"Okafor","role":"admin","auth_version":1},"business":{"id":1,"company_name":"Cauldra Test Shop","currency":"NGN","timezone":"Africa/Lagos"},"permissions":{"sales.create":True},"products":[{"id":1,"name":"Rice","quantity":8,"retail_price":1000}],"suppliers":[],"stocks":[{"product_id":1,"warehouse_id":1,"quantity":8}],"warehouses":[{"id":1,"name":"Main","location_id":1}],"locations":[{"id":1,"name":"Main","is_active":True}],"days":[{"id":5,"location_id":1,"is_open":True}],"sales":[],"expenses":[],"cache":{},"freshness":{}}
            return self.send_bytes(json.dumps(snapshot).encode())
        if path == "/health": return self.send_bytes(b'{"status":"ok"}')
        if path == "/offline-browser-harness.html": return self.send_bytes(HARNESS.read_bytes(),"text/html; charset=utf-8")
        if path in ("/", "/index.html"): return self.send_bytes((FRONTEND / "index.html").read_bytes(), "text/html; charset=utf-8")
        target=(FRONTEND/path.lstrip("/")).resolve()
        if FRONTEND.resolve() not in target.parents or not target.is_file(): return self.send_bytes(b"not found","text/plain",404)
        return self.send_bytes(target.read_bytes(),mimetypes.guess_type(target.name)[0] or "application/octet-stream")

if __name__ == "__main__":
    port=int(sys.argv[1]) if len(sys.argv)>1 else 8768
    print(f"OFFLINE_BROWSER_SERVER http://127.0.0.1:{port}",flush=True)
    ThreadingHTTPServer(("127.0.0.1",port),Handler).serve_forever()

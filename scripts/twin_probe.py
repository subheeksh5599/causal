"""Provision twin-backed services, one per call, and report only their shape.

Twin responses carry session credentials, so the raw JSON goes to .twins/
(gitignored, chmod 600) and this script prints key names and base URLs only.
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".twins"
ENV = ROOT / ".env"


def load_env():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


load_env()
base = (os.environ.get("TWIN_API_URL") or os.environ.get("TWIN_BASE_URL") or "").rstrip("/")
key = os.environ.get("TWIN_API_KEY") or ""
if not base or not key:
    sys.exit("TWIN_API_URL / TWIN_API_KEY missing")
H = {"Authorization": f"Bearer {key}"}
OUT.mkdir(exist_ok=True)
os.chmod(OUT, 0o700)


def redact(obj, depth=0):
    """Show structure without values."""
    if isinstance(obj, dict):
        return {k: redact(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(obj[0], depth + 1)] if obj else []
    if isinstance(obj, str):
        return f"<str {len(obj)}>" if len(obj) > 24 else obj
    return obj


def main():
    print("base:", base)
    r = httpx.get(f"{base}/validate/twins", headers=H, timeout=60)
    print("GET /validate/twins ->", r.status_code)
    data = r.json()
    twins = data.get("twins", data) if isinstance(data, dict) else data
    if isinstance(twins, list):
        names = sorted(t.get("name") or t.get("id") or str(t) for t in twins)
    elif isinstance(twins, dict):
        names = sorted(twins)
    else:
        names = [str(twins)[:100]]
    print("available:", names)

    wanted = sys.argv[1:] or []
    for name in wanted:
        print(f"\n--- provisioning {name} ---")
        for path in ("/validate/twins/provision", "/twins/provision"):
            try:
                pr = httpx.post(f"{base}{path}", headers=H, json={"twins": [name]}, timeout=90)
            except Exception as e:
                print(f"  {path}: {type(e).__name__}"); continue
            print(f"  POST {path} -> {pr.status_code}")
            if pr.status_code >= 400:
                print("  body:", pr.text[:300])
                continue
            body = pr.json()
            print("  shape:", json.dumps(redact(body), indent=2)[:700])
            run_id = body.get("run_id") or body.get("id") or (body.get("run") or {}).get("id")
            if run_id:
                for _ in range(40):
                    s = httpx.get(f"{base}/validate/twins/runs/{run_id}", headers=H, timeout=60)
                    st = s.json()
                    state = st.get("status") or st.get("state")
                    if state in {"ready", "running", "failed", "error"}:
                        print(f"  run {run_id}: {state}")
                        print("  run shape:", json.dumps(redact(st), indent=2)[:900])
                        break
                    time.sleep(2)
            (OUT / f"{name}.json").write_text(json.dumps(body, indent=2))
            os.chmod(OUT / f"{name}.json", 0o600)
            print(f"  raw response saved to .twins/{name}.json")
            break


if __name__ == "__main__":
    main()

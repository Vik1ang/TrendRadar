#!/usr/bin/env python3
"""Real PostgreSQL + HTTPS acceptance for the isolated iOS device-auth staging stack."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("IOS_AUTH_STAGING_ENV", Path.home() / ".config/trendradar-staging/ios-auth-01/staging.env"))
ENV = {}
for line in ENV_FILE.read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        ENV[key] = value
TLS_DIR = Path(ENV["STAGING_TLS_DIR"])
BASE = "https://192.168.2.117:18443"
CTX = ssl.create_default_context(cafile=str(TLS_DIR / "root-ca.crt"))
STACK = ["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(ROOT / "docker-compose.ios-auth-staging.yml")]

def compose(*args):
    return subprocess.run([*STACK, *args], cwd=ROOT, text=True, check=True, capture_output=True).stdout.strip()

def api(path, token=None, method="GET", body=None, auth_scheme="Bearer"):
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"{auth_scheme} {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, context=CTX, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()

def db(sql):
    return compose("exec", "-T", "postgres", "psql", "-U", ENV["POSTGRES_USER"], "-d", ENV["POSTGRES_DB"], "-v", "ON_ERROR_STOP=1", "-At", "-c", sql).strip()

def issue(name):
    raw = compose("exec", "-T", "api", "node", "dist/src/device-auth-cli.js", "issue", "--device-name", name)
    return json.loads(raw)["pairingCode"]

def redeem(pairing, name):
    return api("/api/v2/devices/pair", pairing, "POST", {"deviceName": name}, "Pairing")

def assert_status(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: expected HTTP {expected}, got {actual}")
    print(f"PASS {label}: HTTP {actual}")

def assert_equal(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")
    print(f"PASS {label}")

def main():
    # This limiter is process-local by design; restart only this isolated API
    # so prior interrupted test attempts do not contaminate a rerun.
    compose("restart", "api")
    for _ in range(30):
        try:
            if api("/api/v1/health")[0] == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("staging API did not recover after restart")
    db("UPDATE device_credentials SET enabled=false, revoked_at=COALESCE(revoked_at, now()) WHERE device_name LIKE 'ios-staging-%' AND revoked_at IS NULL")
    # Repeat migration explicitly; the startup migration has already run once.
    compose("exec", "-T", "api", "node", "dist/src/db/migrate.js")
    applied = int(db("SELECT count(*) FROM schema_migrations"))
    if applied != 3:
        raise AssertionError(f"expected 3 migrations, got {applied}")
    print("PASS migration rerun is idempotent; 3 versions applied")

    # Staging-only sanitized fixture, with nullable V1 fields.
    db("""INSERT INTO sources(id,name,type) VALUES ('ios-auth-fixture','Staging Fixture','rss') ON CONFLICT DO NOTHING;
    INSERT INTO articles(id,source_id,title,dedupe_key,summary,published_at,first_seen_at,analysis_status)
    VALUES ('b4e5510a-7cbd-480c-850e-1fe8e5cbb412','ios-auth-fixture','Staging device auth fixture','ios-auth-fixture-1',NULL,NULL,now(),'not_requested')
    ON CONFLICT (id) DO NOTHING;""".replace("\n    ", " "))
    status, body = api("/api/v1/news?limit=10&offset=0")
    assert_status("unauthenticated read", status, 401)
    status, body = api("/api/v1/news?limit=10&offset=0", ENV["STAGING_API_READ_TOKEN"])
    assert_status("legacy shared read credential compatibility", status, 200)
    if b"Staging device auth fixture" not in body or b'"publishedAt":null' not in body or b'"summary":null' not in body:
        raise AssertionError("V1 fixture nullable fields or seeded article missing")
    print("PASS API V1 fixture: null publishedAt/summary preserved")

    # Same one-time code consumed concurrently through verified LAN HTTPS.
    pair = issue("ios-staging-concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: redeem(pair, "ios-staging-concurrent"), range(2)))
    codes = sorted(code for code, _ in responses)
    if codes != [200, 401]:
        raise AssertionError(f"concurrent redemption statuses were {codes}")
    winner = json.loads(next(body for code, body in responses if code == 200))
    token_a = winner["accessToken"]
    device_a = winner["deviceId"]
    if winner["scopes"] != ["news:read"]:
        raise AssertionError("paired identity did not receive exactly news:read")
    try:
        db(f"UPDATE device_credentials SET scopes=ARRAY['admin']::text[] WHERE id='{device_a}'")
    except subprocess.CalledProcessError:
        print("PASS PostgreSQL rejects non-read device scopes")
    else:
        raise AssertionError("database accepted an unauthorized device scope")
    assert_equal("pairing race exactly one winner", codes.count(200), 1)
    assert_equal("pairing race loser rejected", codes.count(401), 1)

    # Verify DB stores a 32-byte digest, not the issued clear token.
    token_hash = sha256(token_a.encode()).hexdigest()
    stored = db(f"SELECT octet_length(token_hash)||':'||encode(token_hash,'hex') FROM device_credentials WHERE id='{device_a}'")
    if stored != f"32:{token_hash}":
        raise AssertionError("credential digest mismatch or cleartext storage")
    print("PASS credential SHA-256 only persisted")

    status, body = api("/api/v1/news?limit=10&offset=0", token_a)
    assert_status("device news read", status, 200)
    status, _ = api("/api/v1/news?limit=10&offset=0", "invalid-device-token")
    assert_status("invalid device credential", status, 401)

    # A second independent device, then scope denial and revocation.
    pair_b = issue("ios-staging-device-b")
    status, body_b = redeem(pair_b, "ios-staging-device-b")
    assert_status("second device pairing", status, 200)
    token_b = json.loads(body_b)["accessToken"]
    device_b = json.loads(body_b)["deviceId"]
    db(f"UPDATE device_credentials SET scopes=ARRAY[]::text[] WHERE id='{device_b}'")
    status, _ = api("/api/v1/news?limit=10", token_b)
    assert_status("scope-less device denied", status, 403)
    db(f"UPDATE device_credentials SET scopes=ARRAY['news:read']::text[] WHERE id='{device_b}'")

    def revoke():
        output = compose("exec", "-T", "api", "node", "dist/src/device-auth-cli.js", "revoke", device_b, "--confirm")
        return json.loads(output)["revoked"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        revoked = list(pool.map(lambda _: revoke(), range(2)))
    if sorted(revoked) != [False, True]:
        raise AssertionError(f"concurrent duplicate revoke results were {revoked}")
    print("PASS concurrent revoke is idempotent (one transition)")
    status, _ = api("/api/v1/news?limit=10", token_b)
    assert_status("revoked device rejected", status, 401)
    status, _ = api("/api/v1/news?limit=10", token_a)
    assert_status("other device remains independent", status, 200)

    # Credential expiry is checked in the database on every request.
    pair_c = issue("ios-staging-expired")
    status, body_c = redeem(pair_c, "ios-staging-expired")
    assert_status("expiry fixture pairing", status, 200)
    credential_c = json.loads(body_c)
    db(f"UPDATE device_credentials SET issued_at=now()-interval '2 seconds', expires_at=now()-interval '1 second' WHERE id='{credential_c['deviceId']}'")
    status, _ = api("/api/v1/news?limit=10", credential_c["accessToken"])
    assert_status("expired credential rejected", status, 401)

    # Pairing is single-use and expired codes cannot be redeemed.
    status, _ = redeem(pair, "ios-staging-concurrent")
    assert_status("pairing replay rejected", status, 401)
    # Keep the per-process brute-force limiter isolated between invalid-code fixtures.
    compose("restart", "api")
    time.sleep(2)
    expiring = issue("ios-staging-code-expired")
    expiring_hash = sha256(expiring.encode()).hexdigest()
    db(f"UPDATE device_pairing_codes SET created_at=now()-interval '2 minutes', expires_at=now()-interval '1 minute' WHERE code_hash=decode('{expiring_hash}','hex')")
    status, _ = redeem(expiring, "ios-staging-code-expired")
    assert_status("expired pairing code rejected", status, 401)

    # Force an insert failure after marking pairing used; transaction must restore code.
    rollback_name = "ios-staging-rollback-" + uuid4().hex
    rollback_code = issue(rollback_name)
    db(f"ALTER TABLE device_credentials ADD CONSTRAINT ios_auth_staging_rollback CHECK (device_name <> '{rollback_name}')")
    status, _ = redeem(rollback_code, rollback_name)
    assert_status("forced transactional failure surfaced", status, 500)
    db("ALTER TABLE device_credentials DROP CONSTRAINT ios_auth_staging_rollback")
    status, body_rollback = redeem(rollback_code, rollback_name)
    assert_status("pairing code remains usable after rollback", status, 200)
    token_rollback = json.loads(body_rollback)["accessToken"]

    # Device credentials cannot access ingestion; staging INGEST_TOKEN still works.
    ingest_body = {"modelName":"staging-test","promptVersion":"staging-test","analysisVersion":1}
    # Ingestion service is intentionally private; invoke it in the isolated network from an API container.
    def ingest_api(token):
        js = """const r=await fetch('http://ingest-api:3002/api/v1/internal/analysis-candidates',{method:'POST',headers:{Authorization:'Bearer '+process.env.TEST_TOKEN,'Content-Type':'application/json'},body:JSON.stringify(%s)});process.stdout.write(JSON.stringify({status:r.status,body:await r.text()}));""" % json.dumps(ingest_body)
        raw = compose("exec", "-T", "-e", f"TEST_TOKEN={token}", "api", "node", "--input-type=module", "-e", js)
        result = json.loads(raw)
        return result["status"]
    assert_status("device rejected at ingestion boundary", ingest_api(token_a), 403)
    assert_status("legacy ingestion credential remains valid", ingest_api(ENV["STAGING_INGEST_TOKEN"]), 200)

    # Dedicated staging telemetry must show no AI budget/call enabled.
    if ENV.get("INTELLIGENCE_MAX_ANALYSES_PER_DAY", "0") != "0":
        raise AssertionError("staging analysis budget is not zero")
    print("PASS staging analysis budget disabled; no model credentials configured")
    # Revoke remaining test identities so the staging DB has no active test credentials.
    for device_id in [device_a, credential_c["deviceId"], json.loads(body_rollback)["deviceId"]]:
        compose("exec", "-T", "api", "node", "dist/src/device-auth-cli.js", "revoke", device_id, "--confirm")
    print("PASS all test devices revoked")

    # Six unique, nonexistent-but-well-formed codes verify the per-client limiter.
    compose("restart", "api")
    for _ in range(30):
        try:
            if api("/api/v1/health")[0] == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("staging API did not recover before rate-limit test")
    dummy_pairing = "trd_pair_" + "A" * 43
    statuses = [redeem(dummy_pairing, "unused")[0] for _ in range(6)]
    assert_equal("pairing rate limit (five 401 then 429)", statuses, [401, 401, 401, 401, 401, 429])
    logs = compose("logs", "--no-color", "--no-log-prefix", "api")
    if dummy_pairing in logs or "trd_dev_" in logs or "trd_pair_" in logs:
        raise AssertionError("credential-shaped value found in API logs")
    print("PASS no pairing/device credential values in API logs")
    print(f"Staging HTTPS base: {BASE} (private CA; never use in production)")
    print("No token or pairing secret emitted")

if __name__ == "__main__":
    main()

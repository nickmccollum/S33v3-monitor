#!/usr/bin/env python3
"""
Proof of concept: Login to Arris S33v3 modem via HNAP JSON
and attempt to retrieve channel information.
"""

import hmac
import hashlib
import time
import os
import sys
import requests
import urllib3
import json

# Suppress the InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ CONFIGURATION ============
MODEM_HOST = "192.168.100.1"
MODEM_USER = "admin"
MODEM_PASSWORD = os.environ.get("MODEM_PASSWORD", "")  # Set via environment variable
if not MODEM_PASSWORD:
    print("ERROR: Set MODEM_PASSWORD environment variable")
    print("  export MODEM_PASSWORD='your-modem-password'")
    sys.exit(1)
# =======================================

BASE_URL = f"https://{MODEM_HOST}"
HNAP_URL = f"{BASE_URL}/HNAP1/"


def hex_hmac_sha256(key_str: str, msg_str: str) -> str:
    """HMAC-SHA256 with (key, message) order, returns lowercase hex string."""
    return hmac.new(key_str.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()


def make_hnap_auth(private_key: str, soap_action: str) -> str:
    """Generate HNAP_AUTH header value.
    
    Format: HMAC-SHA256(PrivateKey, timestamp + SOAPAction) + " " + timestamp
    For login requests, private_key should be "withoutloginkey"
    """
    timestamp = str(int(time.time() * 1000))  # milliseconds
    msg = timestamp + f'"http://purenetworks.com/HNAP1/{soap_action}"'
    auth_hash = hmac.new(
        private_key.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256
    ).hexdigest().upper()
    return f"{auth_hash} {timestamp}"


def hnap_request(session: requests.Session, action: str, payload: dict, private_key: str = "withoutloginkey") -> dict:
    """Send an HNAP JSON request and return the parsed response."""
    soap_action = f'"http://purenetworks.com/HNAP1/{action}"'
    headers = {
        "Content-Type": "application/json",
        "SOAPAction": soap_action,
        "HNAP_AUTH": make_hnap_auth(private_key, action),
    }
    resp = session.post(HNAP_URL, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def login(session: requests.Session) -> bool:
    """
    Perform the 2-step HNAP login:
    1. Request challenge (Action=request)
    2. Compute HMAC and submit login (Action=login)
    """
    # Step 1: Request challenge
    print("[*] Step 1: Requesting login challenge...")
    resp1 = hnap_request(session, "Login", {
        "Login": {
            "Action": "request",
            "Username": MODEM_USER,
            "Captcha": ""
        }
    })
    
    login_resp = resp1.get("LoginResponse", {})
    challenge = login_resp.get("Challenge")
    cookie = login_resp.get("Cookie")
    public_key = login_resp.get("PublicKey")
    result = login_resp.get("LoginResult")
    
    print(f"    Challenge: {challenge}")
    print(f"    Cookie: {cookie}")
    print(f"    PublicKey: {public_key}")
    print(f"    LoginResult: {result}")
    
    if not all([challenge, cookie, public_key]):
        print("[!] Failed to get challenge/cookie/publickey from modem")
        return False
    
    # Set the uid cookie (as the JS does)
    session.cookies.set("uid", cookie, path="/")
    
    # Step 2: Compute derived keys and login
    print("[*] Step 2: Computing HMAC and logging in...")
    
    # Debug: show exact inputs
    hmac_key_1 = public_key + MODEM_PASSWORD
    print(f"    HMAC1 key: '{public_key}' + password (len={len(MODEM_PASSWORD)})")
    print(f"    HMAC1 msg: '{challenge}'")
    
    # PrivateKey = hex_hmac_sha256(PublicKey + Password, Challenge).upper()
    private_key = hex_hmac_sha256(public_key + MODEM_PASSWORD, challenge).upper()
    print(f"    PrivateKey (full): {private_key}")
    
    # JS also sets PrivateKey cookie
    session.cookies.set("PrivateKey", private_key, path="/")
    
    print(f"    HMAC2 key: PrivateKey (64 hex chars)")
    print(f"    HMAC2 msg: '{challenge}'")
    
    # LoginPassword = hex_hmac_sha256(PrivateKey, Challenge).upper()
    login_password = hex_hmac_sha256(private_key, challenge).upper()
    print(f"    LoginPassword (full): {login_password}")
    
    resp2 = hnap_request(session, "Login", {
        "Login": {
            "Action": "login",
            "Username": MODEM_USER,
            "LoginPassword": login_password,
            "Captcha": ""
        }
    })
    
    login_result = resp2.get("LoginResponse", {}).get("LoginResult")
    print(f"    LoginResult: {login_result}")
    
    if login_result in ("OK", "OK_CHANGED"):
        print("[+] Login successful!")
        return private_key  # Return the private key for subsequent calls
    else:
        print(f"[!] Login failed: {login_result}")
        return None


def try_hnap_call(session: requests.Session, method_name: str, private_key: str):
    """Try calling an HNAP method and return the response."""
    try:
        payload = {method_name: {}}
        resp = hnap_request(session, method_name, payload, private_key)
        return resp
    except Exception as e:
        return {"error": str(e)}


def get_multiple_hnaps(session: requests.Session, methods: list, private_key: str):
    """Call GetMultipleHNAPs with multiple sub-requests."""
    try:
        # Build the payload like the browser does
        inner = {method: "" for method in methods}
        payload = {"GetMultipleHNAPs": inner}
        resp = hnap_request(session, "GetMultipleHNAPs", payload, private_key)
        return resp
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 60)
    print("Arris S33v3 Modem - HNAP Login Proof of Concept")
    print("=" * 60)
    
    # Create session with common browser headers
    session = requests.Session()
    session.verify = False  # Self-signed cert
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE_URL}/Login.html",
    })
    
    # Seed the session (needed to satisfy cookie gating)
    print("[*] Seeding session by fetching Login.html...")
    session.get(f"{BASE_URL}/Login.html", timeout=5)
    print(f"    Cookies after seed: {[c.name for c in session.cookies]}")
    
    # Attempt login
    private_key = login(session)
    if not private_key:
        print("\n[!] Cannot proceed without successful login.")
        return
    
    print("\n" + "=" * 60)
    print("Fetching channel info via GetMultipleHNAPs...")
    print("=" * 60)
    
    # Fetch channel info
    print("\n[*] Calling GetMultipleHNAPs with downstream/upstream channel info...")
    result = get_multiple_hnaps(session, [
        "GetCustomerStatusDownstreamChannelInfo",
        "GetCustomerStatusUpstreamChannelInfo",
    ], private_key)
    
    result_str = json.dumps(result, indent=2)
    if len(result_str) > 2000:
        print(f"    Response (truncated):\n{result_str[:2000]}...")
    else:
        print(f"    Response:\n{result_str}")
    
    # Fetch event logs
    print("\n" + "=" * 60)
    print("Fetching Event Logs via GetCustomerStatusLog...")
    print("=" * 60)
    
    result = get_multiple_hnaps(session, ["GetCustomerStatusLog"], private_key)
    
    # Response is directly at GetCustomerStatusLogResponse
    log_resp = result.get("GetCustomerStatusLogResponse", {})
    log_raw = log_resp.get("CustomerStatusLogList", "")
    
    # Parse logs
    entries = []
    for entry in log_raw.split("}-{"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("^")
        if len(parts) >= 5:
            entries.append({
                "timestamp": parts[1],
                "level": parts[3],
                "message": "^".join(parts[4:])
            })
    
    print(f"\nParsed {len(entries)} event log entries:")
    for e in entries[:10]:  # Show first 10
        level_color = {"Critical": "🔴", "Warning": "🟡", "Notice": "🔵"}.get(e["level"], "⚪")
        print(f"  {level_color} [{e['level']}] {e['timestamp']}: {e['message'][:80]}...")


if __name__ == "__main__":
    main()

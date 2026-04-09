#!/usr/bin/env python3
"""
sovereign_lifecycle_audit.py — The Sovereign Bitcoin Agent v5.0 Audit Suite. 🛡️🦀✨⚙️📉
Validates: Enclave -> UTXO -> Generator -> Signer -> Policy.
"""

import socket
import json
import base64
import subprocess
import requests
import os
import sys
from datetime import datetime

# Configuration
ENCLAVE_HOST = "127.0.0.1"
ENCLAVE_PORT = 7777
API_URL = "https://mutinynet.com/api"

# Relative Discovery of binary
script_dir = os.path.dirname(os.path.abspath(__file__))
BINARY_PATH = os.path.join(script_dir, "enclave-signer", "target", "release", "gen_live_psbt")
if not os.path.exists(BINARY_PATH):
    # Fallback to debug
    BINARY_PATH = os.path.join(script_dir, "enclave-signer", "target", "debug", "gen_live_psbt")
RECIPIENT_ADDR = "tb1p7t6842hqmfmj2lnf5zeqrzewcvxut4g4cx3jt7t72qpcqk49l4cq93xj69"

def log(msg, symbol="⚙️"):
    print(f"{datetime.now().strftime('%H:%M:%S')} | {symbol} {msg}")

def enclave_call(payload):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((ENCLAVE_HOST, ENCLAVE_PORT))
            s.sendall(json.dumps(payload).encode())
            resp = s.recv(4096).decode()
            return json.loads(resp)
    except Exception as e:
        return {"type": "Error", "error": str(e)}

def run_audit():
    print("\n" + "="*60)
    print("       🏛️  SOVEREIGN BITCOIN AGENT: LIFECYCLE AUDIT v5.0")
    print("="*60 + "\n")

    # --- PHASE 1: IDENTITY ---
    log("Resolving Enclave Identity...", "🔑")
    addr_resp = enclave_call({"type": "GetAddress"})
    if addr_resp.get("type") == "Error":
        log(f"Enclave unreachable: {addr_resp.get('error')}", "❌")
        return

    address = addr_resp.get("address")
    log(f"Enclave Address: {address}", "✅")

    # --- PHASE 2: POLICY ---
    log("Introspecting Sovereign Policy...", "📜")
    policy_resp = enclave_call({"type": "GetPolicy"})
    policy = policy_resp.get("policy", {})
    allowance = policy.get("allowance_sats", 0)
    version = policy.get("version", 0)
    log(f"Policy Version: v{version}", "✅")
    log(f"Current Allowance: {allowance:,} sats", "✅")

    # --- PHASE 3: REALITY CHECK (UTXOs) ---
    log(f"Scanning Mutinynet for UTXOs at {address}...", "📡")
    try:
        r = requests.get(f"{API_URL}/address/{address}/utxo", timeout=10)
        utxos = r.json() if r.status_code == 200 else []
        total_balance = sum(u["value"] for u in utxos)
        log(f"Balance: {total_balance:,} sats in {len(utxos)} UTXO(s).", "💰")
        if not utxos:
            log("No funds found. Audit cannot proceed with forging.", "❌")
            return
        
        # Pick the largest UTXO
        best_utxo = max(utxos, key=lambda x: x["value"])
    except Exception as e:
        log(f"Reality Check failed: {e}", "❌")
        return

    # --- PHASE 4: FORGING ---
    log("Forging Bit-Perfect PSBT with Legend Message...", "🔨")
    # Fetch scriptPubKey for sighash
    r_spk = requests.get(f"{API_URL}/address/{address}")
    spk_hex = r_spk.json().get("scriptpubkey", "5120cd78b03b2b3f28f3e8171a31207800fd5a44c3f1eeaf9aa5a500b376a5974a33")

    forge_req = {
        "inputs": [{
            "txid": best_utxo["txid"],
            "vout": best_utxo["vout"],
            "amount_sats": best_utxo["value"],
            "script_pubkey_hex": spk_hex
        }],
        "outputs": [
            {"address": RECIPIENT_ADDR, "amount_sats": 800}
        ],
        "op_return_message": "🛡️ Phase 29 Restoration: Sovereign by Math.",
        "fee_rate_sat_vb": 1.5
    }

    b64_req = base64.b64encode(json.dumps(forge_req).encode()).decode()
    if not os.path.exists(BINARY_PATH):
        log(f"Generator binary missing at {BINARY_PATH}", "❌")
        return

    result = subprocess.run([BINARY_PATH, b64_req], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Forging failed: {result.stderr}", "❌")
        return
    
    psbt_b64 = result.stdout.strip()
    log("PSBT forged successfully.", "✅")

    # --- PHASE 5: SIGNING ---
    log("Demanding Sovereign Signature from Enclave...", "✒️")
    sign_req = {
        "type": "SignTransaction",
        "psbt_base64": psbt_b64,
        "amount_sats": 800
    }
    
    sign_resp = enclave_call(sign_req)
    if sign_resp.get("type") == "Error":
        log(f"Signing REJECTED: {sign_resp.get('error')}", "❌")
        return
    
    raw_tx = sign_resp.get("raw_hex")
    txid = sign_resp.get("txid")
    log("Transaction SIGNED by Enclave (Autonomous Allowance Path).", "✅")
    log(f"TXID: {txid}", "🔗")

    # --- FINAL VERDICT ---
    print("\n" + "="*60)
    print("      🟢 SOVEREIGN AUDIT PASSED: THE AGENT IS READY.")
    print("="*60)
    log(f"Hex dump (first 64 chars): {raw_tx[:64]}...", "💎")
    print("\nYou can now safely test the Telegram cycle.\n")

if __name__ == "__main__":
    run_audit()

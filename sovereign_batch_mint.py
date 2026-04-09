#!/usr/bin/env python3
"""
sovereign_batch_mint.py — Sovereign Bitcoin Agent Batch Orchestrator
Interactive Edition 🗿🛡️⛓️⚙️

This script handles the full lifecycle of a 25-transaction Daisy-Chain batch.
It requires manual cryptographic authorization (the Mandate) via local signing.
"""

import os
import sys
import time
import json
import base64
import hashlib
import requests
import subprocess
from typing import Optional, Dict, List
from dotenv import load_dotenv

# ── Cypherpunk UI Setup ──────────────────────────────────────────────────────
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
GOLD = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

def log_status(msg: str):
    print(f"{CYAN}⚙️  {msg}{RESET}")

def log_success(msg: str):
    print(f"{GREEN}✅ {msg}{RESET}")

def log_warning(msg: str):
    print(f"{GOLD}⚠️  {msg}{RESET}")

def log_error(msg: str):
    print(f"{RED}🚨 {msg}{RESET}")

def log_header(msg: str):
    print(f"\n{BOLD}{GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"       {msg}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}\n")

# ── Environment & Config ──────────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT_DIR, ".env")
load_dotenv(ENV_PATH)

# Add bridge to path
BRIDGE_DIR = os.path.join(ROOT_DIR, "mcp-bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

NETWORK = os.getenv("BITCOIN_NETWORK", "signet")
API_URL = "https://mempool.space/api" if NETWORK == "mainnet" else "https://mutinynet.com/api"
API_WEB = "https://mempool.space" if NETWORK == "mainnet" else "https://mutinynet.com"

# Protocol Constants
DUST_SATS = 546
PROTOCOL_FEE_SATS = 1337
EST_VSIZE_PER_TX = 160  # 1-in-4-out Taproot transaction

# ── Core Operations ──────────────────────────────────────────────────────────

def fetch_fee_rate() -> float:
    """Fetch optimal fee rate for next block."""
    try:
        resp = requests.get(f"{API_URL}/v1/fees/recommended", timeout=10)
        fees = resp.json()
        return float(fees.get("fastestFee", 1.0))
    except:
        return 1.0

def check_tx_mempool(txid: str) -> bool:
    """Check if TX is in mempool or confirmed."""
    try:
        resp = requests.get(f"{API_URL}/tx/{txid}", timeout=5)
        return resp.status_code == 200
    except:
        return False

def progress_bar(current: int, total: int, txid: str = ""):
    percent = int((current / total) * 100)
    filled = int((current / total) * 20)
    bar = "█" * filled + "░" * (20 - filled)
    sys.stdout.write(f"\r{CYAN}[{bar}] {current}/{total} Transactions | TX: {txid[:8]}...{txid[-4:] if txid else ''} ({percent}%){RESET}")
    sys.stdout.flush()

# ── Main Application ──────────────────────────────────────────────────────────

def main():
    log_header("🏛️  SOVEREIGN BITCOIN AGENT: BATCH ORCHESTRATOR")
    
    # 1. Initialization & Reconnaissance
    log_status(f"Identity check on {BOLD}{NETWORK.upper()}{RESET}...")
    
    try:
        from bridge import EnclaveBridge
        bridge = EnclaveBridge(port=7777)
        policy = bridge.get_policy()
        
        if policy.get("type") == "Error":
            log_error(f"Enclave offline: {policy.get('error')}")
            sys.exit(1)
            
        agent_address = policy.get("address")
        agent_spk = policy.get("script_pubkey_hex")
        log_success(f"Enclave Online. Identity verified.")
        print(f"Agent Address: {BOLD}{GREEN}{agent_address}{RESET}")
        
        # Check Balance
        r = requests.get(f"{API_URL}/address/{agent_address}/utxo", timeout=10)
        utxos = r.json() if r.status_code == 200 else []
        balance = sum(u["value"] for u in utxos)
        print(f"Current Balance: {BOLD}{GOLD}{balance:,} sats{RESET}")
        
        if not utxos:
            log_error("No UTXOs identified. Agent requires funding before batching.")
            sys.exit(1)
            
    except Exception as e:
        log_error(f"Reconnaissance failed: {e}")
        sys.exit(1)

    # 2. Parameters Input
    log_header("⚙️  BATCH CONFIGURATION")
    rune_id = input(f"Which Rune would you like to mint? (ID/Name)\n> ").strip()
    if not rune_id:
        log_error("Rune ID is required.")
        sys.exit(1)
        
    try:
        count_str = input(f"How many transactions in this batch? (Max 25)\n> ").strip()
        count = min(int(count_str or 25), 25)
    except ValueError:
        count = 25

    # 3. Quotation
    fee_rate = fetch_fee_rate()
    network_fee_per_tx = int(EST_VSIZE_PER_TX * fee_rate)
    total_per_tx = DUST_SATS + PROTOCOL_FEE_SATS + network_fee_per_tx
    total_batch_cost = total_per_tx * count
    
    log_header("💰 QUOTATION")
    print(f"Fee Rate      : {BOLD}{fee_rate} sat/vB{RESET}")
    print(f"Per TX Cost   : {total_per_tx:,} sats (Dust+Protocol+Fee)")
    print(f"Total Batch   : {BOLD}{total_batch_cost:,} sats{RESET}")
    
    if balance < total_batch_cost:
        log_error(f"Insufficient funds. Needs {total_batch_cost:,} sats, has {balance:,} sats.")
        sys.exit(1)
        
    confirm_quote = input(f"\nProceed to generate Mandate Manifest? (y/n): ").strip().lower()
    if confirm_quote != 'y':
        log_warning("Operation cancelled by user.")
        sys.exit(0)

    # 4. Mandate Handshake (State 1)
    log_header("🔐 THE SOVEREIGN HANDSHAKE")
    
    manifest = {
        "batch_id": f"batch-{int(time.time())}",
        "count": count,
        "total_fee_sats": total_batch_cost,
        "rune_id": rune_id,
        "protocol_address": os.getenv("MASTER_RECEIVE_ADDRESS", agent_address)
    }
    
    manifest_json = json.dumps(manifest, separators=(',', ':'))
    manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
    
    print(f"Please sign this {BOLD}Mandate Manifest{RESET} using your {BOLD}Master Key{RESET}.\n")
    print(f"{BOLD}MANIFEST HASH (SHA-256):{RESET}")
    print(f"{BOLD}{CYAN}{manifest_hash}{RESET}\n")
    
    print(f"Use this command locally to generate the signature:")
    print(f"{BOLD}python3 scripts/sign_mandate.py '{manifest_json}' YOUR_PRIVATE_KEY{RESET}\n")
    
    mandate_signature = input(f"Paste the resulting {BOLD}Signature Hex{RESET} here:\n> ").strip()
    if not mandate_signature:
        log_error("Signature is required for batch authorization.")
        sys.exit(1)

    # 5. Validation & Execution (State 2)
    log_header("🛡️  MANDATE VALIDATION")
    log_status("Sending mandate to Secure Enclave for verification...")
    
    try:
        # Prepare context PSBT
        best_utxo = max(utxos, key=lambda x: x["value"])
        context_req = {
            "inputs": [{"txid": best_utxo["txid"], "vout": best_utxo["vout"], "amount_sats": best_utxo["value"], "script_pubkey_hex": agent_spk}],
            "outputs": [{"address": agent_address, "amount_sats": 546}],
            "op_return_message": None, "fee_rate_sat_vb": fee_rate, "rune_mint": None
        }
        b64_req = base64.b64encode(json.dumps(context_req).encode()).decode()
        
        # Binary resolution
        bin_path = os.path.join(ROOT_DIR, "enclave-signer", "target", "release", "gen_live_psbt")
        if not os.path.exists(bin_path):
             bin_path = os.path.join(ROOT_DIR, "enclave-signer", "target", "debug", "gen_live_psbt")
        
        res = subprocess.run([bin_path, b64_req], capture_output=True, text=True)
        if res.returncode != 0:
            log_error(f"PSBT Context Build Failed: {res.stderr}")
            sys.exit(1)
        psbt_b64 = res.stdout.strip()
        
        # Enclave Sign
        sign_resp = bridge.sign_batch_chain(psbt_b64, manifest, mandate_signature)
        
        if sign_resp.get("type") == "Error":
            log_error(f"MANDATE REJECTED BY ENCLAVE: {sign_resp.get('error')}")
            sys.exit(1)
            
        signed_txs = sign_resp.get("signed_batch_psbts", [])
        log_success(f"Signature verified by Enclave. {len(signed_txs)} transactions signed.")
        
        confirm_exec = input(f"\nForge and Broadcast the {BOLD}{len(signed_txs)} transaction{RESET} chain? (y/n): ").strip().lower()
        if confirm_exec != 'y':
            log_warning("Launch cancelled. No transactions broadcasted.")
            sys.exit(0)
            
    except Exception as e:
        log_error(f"Validation flow failed: {e}")
        sys.exit(1)

    # 6. Sequential Broadcast Rafale
    log_header("🚀 LAUNCHING DAISY-CHAIN RAFALE")
    
    txids = []
    for i, raw_hex in enumerate(signed_txs):
        try:
            r = requests.post(f"{API_URL}/tx", data=raw_hex)
            if r.status_code == 200:
                txid = r.text.strip()
                txids.append(txid)
                progress_bar(i + 1, len(signed_txs), txid)
                
                # Poll for mempool presence before next step
                if i < len(signed_txs) - 1:
                    max_retries = 10
                    found = False
                    for _ in range(max_retries):
                        time.sleep(1)
                        if check_tx_mempool(txid):
                            found = True
                            break
                    if not found:
                        log_warning(f"\nWaiting for {txid[:8]} to propagate...")
                        time.sleep(2)
            else:
                log_error(f"\nBroadcast failed at Step {i+1}: {r.text}")
                break
        except Exception as e:
            log_error(f"\nBroadcast Exception at Step {i+1}: {e}")
            break

    log_header("🏆 BATCH SUCCESSFUL")
    log_success(f"Successfully broadcasted {len(txids)} transactions.")
    print(f"First TXID: {BOLD}{txids[0]}{RESET}")
    print(f"Last TXID : {BOLD}{txids[-1]}{RESET}")
    print(f"Explorer  : {API_WEB}/address/{agent_address}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperation aborted by user.")
        sys.exit(0)

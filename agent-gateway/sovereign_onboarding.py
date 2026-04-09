#!/usr/bin/env python3
"""
sovereign_onboarding.py — Sovereign Bitcoin Agent Onboarding Wizard
Mainnet Edition 🗿🛡️🪙

This script automates the transition from Mutinynet to Mainnet, guiding the user
through identity creation, funding, and the 'Baptism' Runes mint.
"""

import os
import sys
import time
import json
import base64
import subprocess
import requests
from typing import Optional
from dotenv import load_dotenv, set_key

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

# Load environment
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT_DIR, ".env")
load_dotenv(ENV_PATH)

# Add bridge to path
BRIDGE_DIR = os.path.join(ROOT_DIR, "mcp-bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

# ── Sequence 1: Identity & Parameters ─────────────────────────────────────────

log_header("🏛️  SOVEREIGN BITCOIN AGENT: ONBOARDING WIZARD (MAINNET)")

print(f"{BOLD}Welcome, Collector.{RESET}")
print("You are about to initialize a Sovereign Agent on the Bitcoin Mainnet.")
print("This agent operates within a Secure Enclave, bound by Taproot MAST logic.\n")

# Human Master Key
human_pubkey = input(f"Enter your {BOLD}Mainnet Public Key{RESET} (for the 1-of-2 multisig recovery path):\n> ").strip()
if not human_pubkey or len(human_pubkey) < 64:
    log_error("Invalid public key. A 64-char Hex (X-only) is expected.")
    sys.exit(1)

# Human Receive Address
human_address = input(f"Enter your {BOLD}Mainnet Receive Address{RESET} (where the Agent will deliver minted Runes):\n> ").strip()
if not human_address.startswith("bc1"):
    log_error("Invalid address. A Mainnet address (starting with bc1) is expected.")
    sys.exit(1)

# Authorized Telegram Username
tg_username = input(f"\nEnter the {BOLD}Telegram Username{RESET} authorized to command the Agent (without @):\n> ").strip().lstrip("@")
if not tg_username:
    log_error("A Telegram username is required for operational security.")
    sys.exit(1)

# Save to .env
set_key(ENV_PATH, "MASTER_HUMAN_PUBKEY", human_pubkey)
set_key(ENV_PATH, "MASTER_RECEIVE_ADDRESS", human_address)
set_key(ENV_PATH, "TELEGRAM_ALLOWED_USERNAME", tg_username)
set_key(ENV_PATH, "BITCOIN_NETWORK", "mainnet")
# Ensure the API URL switches to mainnet for the gateway
set_key(ENV_PATH, "MAINNET_API_URL", "https://mempool.space/api")
# 🛡️ PORT FIX: Force bridge to 7777 to match gen_live_psbt and enclave-signer
set_key(ENV_PATH, "ENCLAVE_PORT", "7777")

# 🔑 ENCLAVE SECRET KEY: Auto-generate if not already set
import secrets
existing_key = os.getenv("ENCLAVE_SECRET_KEY", "").strip("'\"")
if existing_key and len(existing_key) == 64:
    log_success(f"Enclave Secret Key: EXISTS (reusing sealed identity)")
else:
    new_key = secrets.token_hex(32)  # 32 bytes = 64 hex chars
    set_key(ENV_PATH, "ENCLAVE_SECRET_KEY", new_key)
    os.environ["ENCLAVE_SECRET_KEY"] = new_key
    log_success(f"Enclave Secret Key: GENERATED (sealed to .env)")
    print(f"\n{RED}⚠️  BACKUP THIS KEY SECURELY. If lost, the Agent's MAST identity is unrecoverable.{RESET}")
    print(f"{BOLD}   Key stored in: {ENV_PATH}{RESET}\n")

log_success(f"Identity anchored. Network set to {BOLD}MAINNET{RESET}.")

# ── Sequence 2: Dependency Verification ───────────────────────────────────────

log_header("🔍 DEPENDENCY VERIFICATION")

def check_binary(cmd: str, name: str):
    try:
        subprocess.run([cmd, "--version"], capture_output=True, check=True)
        log_success(f"{name} is installed.")
    except:
        log_error(f"{name} is missing. Please install it to continue.")
        sys.exit(1)

check_binary("cargo", "Rust (Cargo)")
check_binary("python3", "Python 3")

BIN_PATH = os.path.join(ROOT_DIR, "enclave-signer", "target", "release", "enclave-signer")
if not os.path.exists(BIN_PATH):
    log_warning("Enclave binary not found in release.")
    print(f"Please run: {BOLD}cargo build --release{RESET} in the enclave-signer directory.")
    sys.exit(1)

log_success("All binaries identified. Ready to boot.")

# ── Sequence 3: Boot & Identity (Enclave) ─────────────────────────────────────

log_header("🚀 INITIALIZING ENCLAVE IDENTITY")

# Start Enclave Signer in the background
log_status("Spinning up the Secure Enclave (Port 7777)...")

# 🛡️ Ensure the enclave inherits the correct identity from .env
enclave_env = os.environ.copy()
enclave_env["MASTER_HUMAN_PUBKEY"] = human_pubkey
enclave_env["BITCOIN_NETWORK"] = "mainnet"
# ENCLAVE_SECRET_KEY should already be in os.environ via .env or export

signer_proc = subprocess.Popen(
    [BIN_PATH],
    cwd=os.path.join(ROOT_DIR, "enclave-signer"),
    env=enclave_env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
)

# Wait for enclave to bind (increase for cold start)
time.sleep(5)

try:
    from bridge import EnclaveBridge
    bridge = EnclaveBridge(port=7777)
    policy = bridge.get_policy()
    
    if policy.get("type") == "Error":
        log_error(f"Enclave communication failed: {policy.get('error')}")
        signer_proc.terminate()
        sys.exit(1)
        
    agent_address = policy.get("address")
    log_success(f"Enclave Online. Identity derived.")
    print(f"\n{BOLD}AGENT MAINNET ADDRESS:{RESET}\n{GREEN}{agent_address}{RESET}\n")

except Exception as e:
    log_error(f"Failed to connect to Bridge API: {e}")
    signer_proc.terminate()
    sys.exit(1)

# ── Sequence 4: The Funding Pause ─────────────────────────────────────────────

log_header("💰 THE FUNDING RITUAL")

print(f"{BOLD}CRITICAL STEP:{RESET} You must provide the initial fuel for the agent.")
print(f"Please fund the Agent's internal wallet with at least {BOLD}20,000 sats{RESET}.")
print("(Includes Dust + Protocol Fee + Mining Fees + Safety Margin)\n")

print(f"Scan this address or copy it: {BOLD}{agent_address}{RESET}")
print(f"Monitoring mempool.space for incoming UTXOs...\n")

while True:
    try:
        # Check Mainnet Esplora
        resp = requests.get(f"https://mempool.space/api/address/{agent_address}/utxo", timeout=10)
        utxos = resp.json() if resp.status_code == 200 else []
        
        if utxos:
            total = sum(u["value"] for u in utxos)
            log_success(f"UTXO DETECTED! Received {total:,} sats. Autonomous phase initiated.")
            break
        else:
            print(f"Waiting for funding... ({time.strftime('%H:%M:%S')})", end="\r")
            time.sleep(15)
    except Exception as e:
        log_warning(f"Network error while polling: {e}. Retrying in 30s...")
        time.sleep(30)

# ── Sequence 5: The Baptism (Mainnet Mint) ────────────────────────────────────

log_header("🌊 THE BAPTISM: MAINNET RUNE MINT")

print("The Agent will now prove its sovereignty by minting a test Rune.")
print("This transaction will be signed autonomously within the enclave.\n")

# Prepare environment for the forge binary
env = os.environ.copy()
env["BITCOIN_NETWORK"] = "mainnet"

# Use the bridge's tool caller directly to avoid circular imports of agent_server
log_status("Forging PSBT for 'OP•RETURN•WAR'...")
print(f"Destination: {BOLD}{human_address}{RESET} (Direct Delivery)")

# Payload for gen_live_psbt (Mainnet configuration)
# Note: We use the actual UTXO we just detected
best_utxo = max(utxos, key=lambda x: x["value"])
spk = policy.get("script_pubkey_hex", "")

# Fetch mainnet fee rate with +20% premium
try:
    fr_resp = requests.get("https://mempool.space/api/v1/fees/recommended")
    base_fee = fr_resp.json().get("halfHourFee", 50)
    fee_rate = round(base_fee * 1.2, 2)
    log_status(f"Network Fee: {base_fee} sat/vB | Target (+20%): {fee_rate} sat/vB")
except:
    fee_rate = 12 

# Final destination for the baptism mint (Direct Delivery + Protocol Fee)
# MAINNET_FEE_ADDRESS = bc1pcunemxjlgd94662v39a8waa2unm0rvvlcmr95l96e8fxe6jdzshq32fexd
req = {
    "inputs": [{
        "txid": best_utxo["txid"],
        "vout": best_utxo["vout"],
        "amount_sats": best_utxo["value"],
        "script_pubkey_hex": spk,
    }],
    "outputs": [
        {"address": human_address, "amount_sats": 546},
        {"address": "bc1pcunemxjlgd94662v39a8waa2unm0rvvlcmr95l96e8fxe6jdzshq32fexd", "amount_sats": 1337}
    ],
    "op_return_message": None,
    "fee_rate_sat_vb": fee_rate,
    "rune_mint": {
        "mint_id": "894897:128", 
        "amount": 1,
        "recipient_output": 1,
        "current_height": 900000 
    },
}

try:
    b64_req = base64.b64encode(json.dumps(req).encode()).decode()
    gen_psbt_bin = os.path.join(ROOT_DIR, "enclave-signer", "target", "release", "gen_live_psbt")
    
    psbt_result = subprocess.run([gen_psbt_bin, b64_req], capture_output=True, text=True, env=env)
    if psbt_result.returncode != 0:
        log_error(f"Forge failed: {psbt_result.stderr}")
        sys.exit(1)
        
    psbt_b64 = psbt_result.stdout.strip()
    log_status("Signing Baptismal transaction...")
    
    sign_resp = bridge.sign_transaction(psbt_b64)
    if sign_resp.get("type") == "Error":
        log_error(f"Signing failed: {sign_resp.get('error')}")
        sys.exit(1)
        
    log_success("Transaction SIGNED autonomously by Enclave.")
    txid = sign_resp.get("txid", "N/A")
    raw_hex = sign_resp.get("raw_hex", "")

    # Broadcast (Optional: let user decide if it's real Mainnet BTC)
    confirm_broadcast = input(f"\nBroadcast to MAINNET now? (y/n):\n> ").strip().lower()
    if confirm_broadcast == 'y':
        b_resp = requests.post("https://mempool.space/api/tx", data=raw_hex)
        if b_resp.status_code == 200:
            txid = b_resp.text.strip()
            log_header("🏹 SOVEREIGN AGENT IS ONLINE")
            print(f"TXID: {BOLD}{txid}{RESET}")
            print(f"Explorer: https://mempool.space/tx/{txid}")
        else:
            log_error(f"Broadcast failed: {b_resp.text}")
    else:
        log_warning("Broadcast cancelled. Raw Hex preserved.")
        print(f"HEX: {raw_hex[:128]}...")

except Exception as e:
    log_error(f"Baptism failed: {e}")

# ... Finished ...
print(f"\n{BOLD}{GREEN}Onboarding Complete. Welcome to the Sovereign Era.{RESET}\n")

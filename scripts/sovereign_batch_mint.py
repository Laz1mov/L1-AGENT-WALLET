import requests
import json
import os
import sys
import hashlib
import time
from typing import List, Dict, Optional

# Add mcp-bridge to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'mcp-bridge'))
from bridge import EnclaveBridge

from dotenv import load_dotenv

# Load environment variables from the project root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# ─── Configuration ───────────────────────────────────────────────────────────

NETWORK = os.getenv("BITCOIN_NETWORK", "signet").strip("'\"").lower()
if NETWORK == "mainnet":
    API_URL = os.getenv("MAINNET_API_URL", "https://mempool.space/api").strip("'\"")
else:
    API_URL = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api").strip("'\"")

ENCLAVE_PORT = int(os.getenv("ENCLAVE_PORT", "7777"))

BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

def log_step(msg: str):
    print(f"{CYAN}⚙️  {msg}{RESET}")

def log_success(msg: str):
    print(f"{GREEN}✅ {msg}{RESET}")

def log_error(msg: str):
    print(f"{RED}❌ {msg}{RESET}")

def interactive_prompt(msg: str, default: str = "") -> str:
    prompt = f"{BOLD}{msg}{RESET}"
    if default:
        prompt += f" [{default}]"
    val = input(f"{prompt}: ").strip()
    return val if val else default

# ─── Logic ───────────────────────────────────────────────────────────────────

def get_recommended_fee():
    try:
        resp = requests.get(f"{API_URL}/v1/fees/recommended")
        if resp.status_code != 200:
            return 2
        return resp.json().get('hourFee', 2)
    except:
        return 2

def fetch_utxo(address: str):
    resp = requests.get(f"{API_URL}/address/{address}/utxo")
    if resp.status_code != 200:
        log_error(f"API Error ({resp.status_code}): {resp.text}")
        return []
    return resp.json()

def run_batch_mint():
    print(f"\n{BOLD}{'━'*70}")
    print(f"       🏛️  SOVEREIGN BITCOIN AGENT: BATCH MINT ORCHESTRATOR")
    print(f"       Network: {NETWORK.upper()} | API: {API_URL}")
    print(f"{'━'*70}{RESET}\n")

    bridge = EnclaveBridge(port=ENCLAVE_PORT)
    
    # 1. Identity & Balance
    log_step("Initializing Enclave Connection...")
    
    # 🛡️ DYNAMIC OVERRIDE: Allow using the intermediate vault address if requested
    forced_addr = os.getenv("FORCED_AGENT_ADDRESS", "").strip("'\"")
    
    if forced_addr:
        agent_address = forced_addr
        log_success(f"FORCED Agent Identity: {BOLD}{agent_address}{RESET}")
    else:
        policy_resp = bridge.get_policy()
        if policy_resp.get("type") == "Error":
            log_error(f"Enclave Error: {policy_resp.get('error')}")
            return
        agent_address = policy_resp.get("address")
        log_success(f"Agent Identity: {BOLD}{agent_address}{RESET}")
    
    utxos = fetch_utxo(agent_address)
    balance = sum(u['value'] for u in utxos)
    log_success(f"Confirmed Balance: {BOLD}{balance} sats{RESET}")

    # 2. Parameters
    # 🛡️ HARDCODED: OP•RETURN•WAR — The Sovereign Rune
    rune_id = "894897:128"
    log_success(f"Rune Target: {BOLD}OP•RETURN•WAR{RESET} ({rune_id})")
    
    # 🛡️ RUNE DELIVERY: Where should the minted Runes be sent?
    default_dest = os.getenv("MASTER_RECEIVE_ADDRESS", "").strip("'\"")
    if default_dest:
        dest_prompt = f"Rune delivery address? [{default_dest}]"
    else:
        dest_prompt = "Rune delivery address (bc1p...)"
    dest_input = interactive_prompt(dest_prompt, default_dest)
    if not dest_input or not dest_input.startswith("bc1"):
        log_error("Invalid destination address. Must start with bc1.")
        return
    destination_address = dest_input
    log_success(f"Rune Delivery: {BOLD}{destination_address}{RESET}")
    
    count = int(interactive_prompt("Batch size? (Max 25)", "25"))
    fee_rate = get_recommended_fee()
    
    log_step(f"Calculating Reconnaissance & Estimates (Fee Rate: {fee_rate} sat/vB)...")
    
    # Estimate: Dust (~546) + Protocol (1337) + Network (~160) = ~2043 per tx
    cost_per_tx = 546 + 1337 + int(160 * fee_rate)
    total_required = cost_per_tx * count
    
    print(f"\n{YELLOW}📊 BUDGET ESTIMATE:{RESET}")
    print(f"   Destinations: {count} mints")
    print(f"   Cost per Mint: ~{cost_per_tx} sats")
    print(f"   Total Required: {BOLD}{total_required} sats{RESET}")
    
    if balance < total_required:
        log_error(f"INSUFFICIENT FUNDS. You need {total_required - balance} more sats.")
        log_step(f"Please send funds to: {BOLD}{agent_address}{RESET}")
        return

    # 🛡️ ALIGNMENT: The Rust enclave sign_batch_chain logic ONLY supports a single initial UTXO.
    # We must find a SINGLE UTXO big enough to fund the entire transaction, otherwise we need a manual consolidation.
    utxos = sorted(utxos, key=lambda x: x["value"], reverse=True)
    best_utxo = utxos[0]
    
    if best_utxo['value'] < total_required:
        log_error(f"INSUFFICIENT SINGLE-UTXO FUNDS.")
        log_error(f"Your largest UTXO is {best_utxo['value']} sats, but this batch requires {total_required} sats.")
        log_step(f"You have two options:")
        log_step(f"1. Reduce the batch size.")
        log_step(f"2. Send a single transaction of {total_required} sats to {agent_address}.")
        return

    log_success(f"Selected Sovereign UTXO: {best_utxo['value']} sats for batch.")

    # 3. Create Manifest
    log_step("Forging Master Mandate Manifest...")
    batch_id = hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]
    # 🛡️ PROTOCOL ALIGNMENT: Match Rust struct field order exactly
    from collections import OrderedDict
    manifest = OrderedDict([
        ("batch_id", batch_id),
        ("count", count),
        ("total_fee_sats", total_required),
        ("fee_rate", int(fee_rate)),
        ("rune_id", rune_id),
        ("protocol_address", agent_address),
        ("destination_address", destination_address)
    ])
    
    manifest_json = json.dumps(manifest, separators=(',', ':'))
    manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
    
    print(f"\n{BOLD}📜 THE MASTER MANDATE{RESET}")
    print(f"------------------------------------------------------------")
    print(f"{manifest_json}")
    print(f"------------------------------------------------------------")
    print(f"\n{YELLOW}INSTRUCTIONS:{RESET}")
    print(f"1. Open Unisat or your wallet.")
    print(f"2. Use the 'Sign Message' tool (Taproot Account).")
    print(f"3. Paste the EXACT JSON above as the message.")
    print(f"4. Click 'Sign By ecdsa' (standard message signing).")
    print(f"5. Paste the resulting Base64 signature below.")
    
    mandate_sig = interactive_prompt("Enter the Base64 Signature")
    if not mandate_sig or mandate_sig.lower() == 'q':
        return

    # 4. Enclave Signing
    log_step("Executing Sovereignty: Requesting Enclave Signature Chain...")
    
    # We use the largest single UTXO for the batching sequence
    utxo_args = f"{best_utxo['txid']}:{best_utxo['vout']}:{best_utxo['value']}"
    dummy_cmd = f"cd enclave-signer && cargo run --release --bin gen_recovery_psbt -- {agent_address} {agent_address} 1000 {utxo_args}"
    
    import subprocess
    psbt_resp = subprocess.run(dummy_cmd, shell=True, capture_output=True, text=True)
    if psbt_resp.returncode != 0:
        log_error(f"Initial PSBT Forge Failed: {psbt_resp.stderr}")
        return
    initial_psbt = psbt_resp.stdout.strip()

    sign_resp = bridge.sign_batch_chain(
        psbt_base64=initial_psbt,
        batch_manifest=manifest,
        mandate_signature=mandate_sig
    )
    
    if sign_resp.get("type") == "Error":
        log_error(f"Enclave REJECTED Batch: {sign_resp.get('error')}")
        return

    signed_psbts = sign_resp.get("signed_batch_psbts")
    log_success(f"Batch authorized & signed. {len(signed_psbts)} transactions ready.")

    # 5. Sequential Broadcast
    confirm = interactive_prompt("Broadcast the chain to Mainnet now? (y/n)", "n")
    if confirm.lower() == 'y':
        for i, tx_hex in enumerate(signed_psbts):
            log_step(f"Broadcasting transaction {i+1}/{len(signed_psbts)}...")
            b_resp = requests.post(f"{API_URL}/tx", data=tx_hex)
            if b_resp.status_code == 200:
                txid = b_resp.text
                log_success(f"TX {i+1} Broadast: https://mempool.space/tx/{txid}")
                if i < len(signed_psbts) - 1:
                    log_step("Waiting for mempool visibility...")
                    time.sleep(5) # Wait for propagation
            else:
                log_error(f"Broadcast Failure at step {i+1}: {b_resp.text}")
                break
    else:
        log_step("Broadcast cancelled. Signed transactions preserved in logs/memory.")

if __name__ == "__main__":
    run_batch_mint()

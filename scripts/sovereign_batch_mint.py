import requests
import json
import os
import sys
import hashlib
import time
import subprocess
from typing import List, Dict, Optional

# Add mcp-bridge to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'mcp-bridge'))
from bridge import EnclaveBridge

from dotenv import load_dotenv

# Project Root
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

def audit_on_chain_exposure(address: str) -> bool:
    """
    Mandate 2: On-chain Witness Audit.
    Rule: len(witness) > 1 means the script has been revealed (BURNED).
    """
    log_step(f"Auditing identity exposure for {address}...")
    try:
        # Fetch address transaction history (last 50 txs)
        url = f"{API_URL}/address/{address}/txs"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            log_error(f"Audit lookup failed (Status {response.status_code})")
            return False
            
        txs = response.json()
        for tx in txs:
            if not tx.get("status", {}).get("confirmed"):
                continue # Skip unconfirmed for deterministic audit
                
            for vin in tx.get('vin', []):
                # Check if this input is spending from our address
                if vin.get('prevout', {}).get('scriptpubkey_address') == address:
                    witness = vin.get('witness', [])
                    # Taproot Key Path spend = 1 element (Signature)
                    # Taproot Script Path spend > 1 element (Sig + Script + ControlBlock)
                    if len(witness) > 1:
                        log_error(f"⚠️  IDENTITY COMPROMISED: Observed reveal at Tx {tx['txid']}")
                        return True # 🚨 BURNED
        log_success("Identity is pristine (Key Path only or unspent).")
        return False
    except Exception as e:
        log_error(f"Audit Exception: {e}")
        return False

def rotate_identity():
    """
    Mandate 3: Auto-Rotation. Increment derivation index in .env.
    """
    dotenv_path = os.path.join(ROOT_DIR, '.env')
    index = int(os.getenv("ENCLAVE_DERIVATION_INDEX", "0"))
    new_index = index + 1
    
    log_step(f"🔄 ROTATING IDENTITY: Index {index} -> {new_index}")
    
    # Update .env file
    with open(dotenv_path, 'r') as f:
        lines = f.readlines()
    
    with open(dotenv_path, 'w') as f:
        found = False
        for line in lines:
            if line.startswith("ENCLAVE_DERIVATION_INDEX="):
                f.write(f"ENCLAVE_DERIVATION_INDEX='{new_index}'\n")
                found = True
            else:
                f.write(line)
        if not found:
            f.write(f"ENCLAVE_DERIVATION_INDEX='{new_index}'\n")
            
    # Refresh local state
    os.environ["ENCLAVE_DERIVATION_INDEX"] = str(new_index)
    log_success(f"Identity rotated. Next batch will use Index {new_index}.")
    
    log_step("⏳ Waiting for Enclave to pick up the new identity...")
    time.sleep(1)

def restart_enclave():
    """
    Automates the Secure Enclave restart to pick up the new identity index.
    """
    log_step("🚀 AUTOMATED RESTART: Cycling Secure Enclave...")
    
    bin_path = os.path.join(ROOT_DIR, "enclave-signer", "target", "release", "enclave-signer")
    if not os.path.exists(bin_path):
        log_error("Enclave binary not found. Cannot automate restart.")
        return False
        
    try:
        # Kill existing
        subprocess.run(["pkill", "-f", "enclave-signer"], capture_output=True)
        time.sleep(1)
        
        # Start new in background (inheriting new ENV from .env)
        # We use a detached process to ensure it lives after the script
        with open(os.path.join(ROOT_DIR, "enclave-signer", "enclave.log"), "a") as log:
            subprocess.Popen(
                [bin_path],
                stdout=log,
                stderr=log,
                cwd=os.path.join(ROOT_DIR, "enclave-signer"),
                start_new_session=True
            )
        
        time.sleep(2) # Give it time to bind to port 7777
        log_success("Enclave restarted successfully with new Identity Index.")
        return True
    except Exception as e:
        log_error(f"Restart failed: {e}")
        return False

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
    # ─── IDENTITY AUDIT (MANDATE 2) ───
    policy_resp = bridge.get_policy()
    if policy_resp.get("type") == "Error":
        log_error(f"Enclave Error: {policy_resp.get('error')}")
        return
    agent_address = policy_resp.get("address")
    
    # ⚡ VITAL FIX: Audit the specific address returned by the enclave
    if audit_on_chain_exposure(agent_address):
        log_error("CRITICAL: The current agent identity has been burned on-chain.")
        rotate_identity()
        
        # ─── AUTOMATED RESTART ───
        if restart_enclave():
             # Reload the bridge and address
             bridge = EnclaveBridge(port=ENCLAVE_PORT)
             policy_resp = bridge.get_policy()
             agent_address = policy_resp.get("address")
             log_success(f"NEW PRISTINE IDENTITY ACTIVE: {BOLD}{agent_address}{RESET}")
        else:
            log_step("Please RESTART the Enclave server manually to pick up the new identity index.")
            log_step("Terminating current batch to prevent unsafe minting.")
            return
    
    log_success(f"Agent Identity: {BOLD}{agent_address}{RESET}")
    
    # ─── FUNDING HEARTBEAT ───
    while True:
        utxos = fetch_utxo(agent_address)
        balance = sum(u['value'] for u in utxos)
        log_success(f"Confirmed Balance: {BOLD}{balance} sats{RESET}")
        
        # We perform the estimate later, but we need a baseline check
        if balance > 0:
            break
            
        print(f"\n{YELLOW}⏳ WAITING FOR FUNDS...{RESET}")
        print(f"Please send sats to: {BOLD}{agent_address}{RESET}")
        print(f"I will check again in 20 seconds (Ctrl+C to abort)...")
        time.sleep(20)

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
    
    # ─── BUDGET HEARTBEAT (MANDATE 2 & UX) ───
    while True:
        utxos = fetch_utxo(agent_address)
        # Sort to find the best (largest) single UTXO
        utxos = sorted(utxos, key=lambda x: x["value"], reverse=True)
        
        if not utxos:
            log_error("Wallet is empty.")
            current_best = 0
        else:
            current_best = utxos[0]["value"]
            
        if current_best >= total_required:
            best_utxo = utxos[0]
            log_success(f"Budget Verified: Primary UTXO {current_best} sats found.")
            break
            
        diff = total_required - current_best
        log_error(f"INSUFFICIENT BUDGET. Need a single UTXO of {total_required} sats.")
        if current_best > 0:
            log_warning(f"Your largest UTXO is {current_best} sats. You need {diff} more in a SINGLE output.")
        
        print(f"\n{YELLOW}⏳ WAITING FOR BUDGET COMPLETION...{RESET}")
        print(f"Target Address: {BOLD}{agent_address}{RESET}")
        print(f"Goal: {BOLD}{total_required}{RESET} sats in one transaction.")
        print(f"Check interval: 20 seconds...")
        time.sleep(20)

    log_success(f"Selected Sovereign UTXO: {best_utxo['value']} sats for batch.")

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

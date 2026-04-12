#!/usr/bin/env python3
"""
sovereign_withdrawal.py — Sovereign Recovery: Script Path Withdrawal

This script allows the HUMAN MASTER to withdraw ALL funds from the
Sovereign Agent's Taproot MAST address using the Recovery Leaf (Script Path).

The enclave is NOT required to sign. Only the human master private key is needed.

Flow:
  1. Connect to enclave (read-only) to confirm identity.
  2. Fetch all UTXOs from the agent address.
  3. Forge a PSBT with full BIP-371 Taproot metadata.
  4. Output the PSBT as Base64 for signing in Sparrow/Bitcoin Core.
  5. Optionally accept the signed PSBT back and broadcast.
"""

import os
import sys
import json
import base64
import subprocess
import requests
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


def _read_varint(data: bytes, offset: int):
    prefix = data[offset]
    offset += 1
    if prefix < 0xfd:
        return prefix, offset
    if prefix == 0xfd:
        return int.from_bytes(data[offset:offset + 2], "little"), offset + 2
    if prefix == 0xfe:
        return int.from_bytes(data[offset:offset + 4], "little"), offset + 4
    return int.from_bytes(data[offset:offset + 8], "little"), offset + 8


def _decode_op_return(script_hex: str) -> str:
    try:
        b = bytes.fromhex(script_hex)
        if not b or b[0] != 0x6a:
            return script_hex
        i = 1
        chunks = []
        while i < len(b):
            op = b[i]
            i += 1
            if op <= 75 and i + op <= len(b):
                chunks.append(b[i:i + op])
                i += op
            else:
                break
        msg = b"".join(chunks)
        return msg.decode("utf-8", errors="replace") or script_hex
    except Exception:
        return script_hex


def decode_tx_hex(raw_hex: str) -> str:
    raw_hex = raw_hex.strip()
    data = bytes.fromhex(raw_hex)
    o = 0
    lines = []

    def add_kv(label: str, value: str):
        lines.append(f"{label:<12}: {value}")

    version = int.from_bytes(data[o:o + 4], "little")
    o += 4
    add_kv("Version", str(version))
    add_kv("Raw size", f"{len(data)} bytes")

    segwit = len(data) > o + 1 and data[o] == 0 and data[o + 1] == 1
    if segwit:
        o += 2
        add_kv("SegWit", "yes")
    else:
        add_kv("SegWit", "no")

    vin_count, o = _read_varint(data, o)
    add_kv("Inputs", str(vin_count))
    lines.append("")
    lines.append("INPUTS")
    lines.append("-" * 72)
    for i in range(vin_count):
        prev_txid = data[o:o + 32][::-1].hex(); o += 32
        vout = int.from_bytes(data[o:o + 4], "little"); o += 4
        script_len, o = _read_varint(data, o)
        script_sig = data[o:o + script_len].hex(); o += script_len
        sequence = data[o:o + 4].hex(); o += 4
        lines.append(f"[{i}] {prev_txid}:{vout}")
        lines.append(f"    scriptSig : {script_sig or '(empty)'}")
        lines.append(f"    sequence  : {sequence}")
        lines.append("")

    vout_count, o = _read_varint(data, o)
    add_kv("Outputs", str(vout_count))
    lines.append("")
    lines.append("OUTPUTS")
    lines.append("-" * 72)
    for i in range(vout_count):
        value_sats = int.from_bytes(data[o:o + 8], "little"); o += 8
        spk_len, o = _read_varint(data, o)
        spk = data[o:o + spk_len].hex(); o += spk_len
        value_btc = value_sats / 100_000_000
        lines.append(f"[{i}] {value_sats:,} sats  ({value_btc:.8f} BTC)")
        lines.append(f"    scriptPubKey: {spk}")
        if spk.startswith("6a"):
            lines.append(f"    OP_RETURN   : {_decode_op_return(spk)}")
        lines.append("")

    if segwit:
        lines.append("WITNESS")
        lines.append("-" * 72)
        for i in range(vin_count):
            items, o = _read_varint(data, o)
            lines.append(f"[{i}] items: {items}")
            for j in range(items):
                item_len, o = _read_varint(data, o)
                item = data[o:o + item_len].hex(); o += item_len
                lines.append(f"    [{j}] {item}")
            lines.append("")

    locktime = int.from_bytes(data[o:o + 4], "little")
    add_kv("Locktime", str(locktime))
    return "\n".join(lines)


# Load environment
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT_DIR, ".env")
load_dotenv(ENV_PATH)

NETWORK = os.getenv("BITCOIN_NETWORK", "signet").strip("'\"").lower()
if NETWORK == "mainnet":
    API_URL = os.getenv("MAINNET_API_URL", "https://mempool.space/api").strip("'\"")
else:
    API_URL = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api").strip("'\"")

# ── Main ─────────────────────────────────────────────────────────────────────

log_header("🔓 SOVEREIGN RECOVERY: SCRIPT PATH WITHDRAWAL")

print(f"{BOLD}This tool generates a PSBT for withdrawing funds from the Agent's{RESET}")
print(f"{BOLD}Taproot address using the Recovery Leaf (Script Path).{RESET}")
print(f"The enclave is {RED}NOT{RESET} required to sign. Only your human master key.\n")

# 1. Determine agent address
log_status("Resolving Agent Identity...")

# Try enclave first, then fallback to manual reconstruction
BRIDGE_DIR = os.path.join(ROOT_DIR, "mcp-bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

agent_address = None
try:
    from bridge import EnclaveBridge
    bridge = EnclaveBridge(port=int(os.getenv("ENCLAVE_PORT", "7777")))
    policy_resp = bridge.get_policy()
    if policy_resp.get("type") != "Error":
        agent_address = policy_resp.get("address")
except:
    pass

if not agent_address:
    log_warning("Enclave is offline. Using manual address resolution...")
    # We can reconstruct the address using the binary itself
    # For now, ask the user
    agent_address = input(f"Enter the {BOLD}Agent Taproot Address{RESET} (bc1p...):\n> ").strip()
    if not agent_address.startswith("bc1"):
        log_error("Invalid address.")
        sys.exit(1)

log_success(f"Agent Address: {BOLD}{agent_address}{RESET}")

# 2. Fetch UTXOs
log_status("Scanning UTXOs on the blockchain...")
try:
    resp = requests.get(f"{API_URL}/address/{agent_address}/utxo", timeout=15)
    utxos = resp.json() if resp.status_code == 200 else []
except Exception as e:
    log_error(f"API Error: {e}")
    sys.exit(1)

if not utxos:
    log_error("No UTXOs found. The agent wallet is empty.")
    sys.exit(0)

total_balance = sum(u["value"] for u in utxos)
log_success(f"Found {len(utxos)} UTXO(s) totaling {BOLD}{total_balance:,} sats{RESET}")

for i, u in enumerate(utxos):
    status = "✓ confirmed" if u.get("status", {}).get("confirmed") else "⏳ unconfirmed"
    print(f"  [{i}] {u['txid']}:{u['vout']}  →  {u['value']:,} sats  ({status})")

# 3. Destination
print()
destination = input(f"Enter {BOLD}destination address{RESET} for withdrawal:\n> ").strip()
if not destination.startswith("bc1") and not destination.startswith("tb1"):
    log_error("Invalid destination address.")
    sys.exit(1)

# 4. Fee rate
try:
    fr_resp = requests.get(f"{API_URL}/v1/fees/recommended", timeout=10)
    recommended_fee = fr_resp.json().get("halfHourFee", 2)
except:
    recommended_fee = 2

fee_input = input(f"Fee rate (sat/vB)? [{recommended_fee}]: ").strip()
fee_rate = float(fee_input) if fee_input else float(recommended_fee)

# 5. Build the withdrawal PSBT via the Rust binary
log_header("🔨 FORGING RECOVERY PSBT")

payload = {
    "destination": destination,
    "fee_rate_sat_vb": fee_rate,
    "utxos": [{"txid": u["txid"], "vout": u["vout"], "value": u["value"]} for u in utxos]
}

b64_payload = base64.b64encode(json.dumps(payload).encode()).decode()
gen_bin = os.path.join(ROOT_DIR, "enclave-signer", "target", "release", "gen_sovereign_withdrawal_psbt")

if not os.path.exists(gen_bin):
    log_error(f"Binary not found: {gen_bin}")
    log_error("Run: cargo build --release --bin gen_sovereign_withdrawal_psbt")
    sys.exit(1)

env = os.environ.copy()
env["BITCOIN_NETWORK"] = NETWORK

result = subprocess.run([gen_bin, b64_payload], capture_output=True, text=True, env=env)

if result.returncode != 0:
    log_error(f"PSBT generation failed:\n{result.stderr}")
    sys.exit(1)

# Print the stderr (contains the human-readable info)
for line in result.stderr.strip().split("\n"):
    if line.strip():
        print(f"  {line}")

psbt_b64 = result.stdout.strip()

if not psbt_b64:
    log_error("Empty PSBT output.")
    sys.exit(1)

# 6. Output the PSBT
log_header("📋 RECOVERY PSBT READY")

print(f"{BOLD}Copy the Base64 PSBT below and import it into Sparrow Wallet:{RESET}\n")
print(f"{GREEN}{psbt_b64}{RESET}\n")

# Also save to file for convenience
psbt_file = os.path.join(ROOT_DIR, "recovery_withdrawal.psbt")
with open(psbt_file, "w") as f:
    f.write(psbt_b64)
log_success(f"PSBT also saved to: {BOLD}{psbt_file}{RESET}")

print(f"\n{BOLD}{GOLD}SIGNING INSTRUCTIONS:{RESET}")
print(f"  1. Open {BOLD}Sparrow Wallet{RESET} (or Bitcoin Core).")
print(f"  2. Go to {BOLD}File → Load Transaction → From Text{RESET}.")
print(f"  3. Paste the Base64 PSBT above.")
print(f"  4. Sign with your {BOLD}master private key{RESET}.")
print(f"  5. Copy the {BOLD}signed PSBT{RESET} or {BOLD}finalized hex{RESET}.\n")

# 7. Optional: Accept signed tx and broadcast
signed_input = input(f"Paste the {BOLD}signed/finalized transaction hex{RESET} to preview (or press Enter to skip):\n> ").strip()

if signed_input:
    try:
        preview_text = []
        preview_text.append("SOVEREIGN WITHDRAWAL — TX PREVIEW")
        preview_text.append("=" * 72)
        preview_text.append(decode_tx_hex(signed_input))
        preview_text.append("")
        preview_text.append("RAW HEX")
        preview_text.append("-" * 72)
        preview_text.append(signed_input)
        preview_text.append("=" * 72)
        preview_text.append("")

        preview_file = os.path.join(ROOT_DIR, "withdrawal_tx_preview.txt")
        with open(preview_file, "w") as f:
            f.write("\n".join(preview_text))

        print(f"\n{BOLD}{GOLD}📋 WITHDRAWAL TX VERIFICATION PREVIEW{RESET}")
        print(f"Saved to: {BOLD}{preview_file}{RESET}\n")
        print("\n".join(preview_text))
    except Exception as e:
        log_error(f"Could not render transaction preview: {e}")
        print(signed_input)

    confirm_broadcast = input(f"\nBroadcast this transaction to network now? (y/n):\n> ").strip().lower()
    if confirm_broadcast == 'y':
        log_status("Broadcasting to network...")
        try:
            b_resp = requests.post(f"{API_URL}/tx", data=signed_input, timeout=30)
            if b_resp.status_code == 200:
                txid = b_resp.text.strip()
                log_header("🏹 WITHDRAWAL COMPLETE")
                print(f"TXID: {BOLD}{txid}{RESET}")
                if NETWORK == "mainnet":
                    print(f"Explorer: https://mempool.space/tx/{txid}")
                else:
                    print(f"Explorer: https://mutinynet.com/tx/{txid}")
            else:
                log_error(f"Broadcast failed: {b_resp.text}")
        except Exception as e:
            log_error(f"Network error: {e}")
    else:
        log_warning("Broadcast skipped. You can broadcast the signed hex manually.")
else:
    log_warning("Broadcast skipped. You can broadcast the signed hex manually.")

print(f"\n{BOLD}{GREEN}Recovery process complete.{RESET}\n")

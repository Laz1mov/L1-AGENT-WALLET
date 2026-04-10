#!/usr/bin/env python3
"""
mcp-bridge/intelligence_server.py
Sovereign Bitcoin Operations Server

Exposes intelligence and batch minting orchestration tools.
Critical operations require a Master Mandate signature (human-in-the-loop).
"""

import os
import sys
import json
import logging
import requests
import hashlib
import time
from collections import OrderedDict
from typing import List, Optional

# Log to stderr to avoid corrupting MCP stdio stream
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OPS] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("SovereignMCP")

# Path to the bridge
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

try:
    from mcp.server.fastmcp import FastMCP
    from bridge import EnclaveBridge
except ImportError as e:
    logger.critical(f"Missing dependencies: {e}")
    sys.exit(1)

# ── Network Config ─────────────────────────────────────────────────────────────

ROOT_DIR = os.path.dirname(BRIDGE_DIR)
env_path = os.path.join(ROOT_DIR, ".env")
if os.path.exists(env_path):
    from dotenv import load_dotenv
    load_dotenv(env_path)

NETWORK = os.getenv("BITCOIN_NETWORK", "signet").strip("'\" ").lower()
if NETWORK == "bitcoin" or NETWORK == "mainnet":
    API_BASE = "https://mempool.space/api"
    EXPLORER = "https://mempool.space"
    NETWORK = "mainnet"
else:
    API_BASE = "https://mutinynet.com/api"
    EXPLORER = "https://mutinynet.com"
    NETWORK = "mutinynet"

logger.info(f"Network: {NETWORK.upper()} — API: {API_BASE}")

# ── Initialisation ────────────────────────────────────────────────────────────

mcp = FastMCP("Sovereign Bitcoin Operations")
_bridge = EnclaveBridge()

# ── Internal Helpers ──────────────────────────────────────────────────────────

def _get(path: str, timeout: int = 10) -> dict | list | None:
    """GET request to mempool API, returns None on error."""
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        logger.warning(f"API {path} → HTTP {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"API {path} → {e}")
        return None


def _agent_address() -> str | None:
    """Retrieve the agent address from the enclave (read-only)."""
    try:
        resp = _bridge.get_policy()
        return resp.get("address")
    except Exception as e:
        logger.error(f"get_policy failed: {e}")
        return None


# ── MCP Tools (INTELLIGENCE) ──────────────────────────────────────────────────

@mcp.tool()
def get_bitcoin_address() -> str:
    """
    RARE SKILL: Returns the current Taproot (P2TR) Bitcoin address of your enclave.
    This is the address you must fund to enable agent operations.
    """
    addr = _agent_address()
    if not addr:
        return "Error: Enclave unreachable or invalid address."
    return f"Agent Address ({NETWORK}): {addr}\nExplorer: {EXPLORER}/address/{addr}"


@mcp.tool()
def get_agent_address() -> str:
    """Alias for get_bitcoin_address."""
    return get_bitcoin_address()


@mcp.tool()
def get_balance() -> str:
    """
    Returns the agent's live balance in satoshis with UTXO details.
    Queries the mempool/mutinynet API directly.
    """
    addr = _agent_address()
    if not addr:
        return "Error: Agent address not found (Enclave offline?)."

    utxos = _get(f"/address/{addr}/utxo")
    if utxos is None:
        return "Error: Mempool API unreachable."

    if not utxos:
        return f"Balance: 0 sats — no UTXOs available.\nAddress: {addr}"

    confirmed = [u for u in utxos if u.get("status", {}).get("confirmed")]
    unconfirmed = [u for u in utxos if not u.get("status", {}).get("confirmed")]

    total = sum(u["value"] for u in utxos)
    total_conf = sum(u["value"] for u in confirmed)

    lines = [
        f"Total Balance    : {total:,} sats",
        f"  Confirmed      : {total_conf:,} sats ({len(confirmed)} UTXOs)",
        f"  Unconfirmed    : {total - total_conf:,} sats ({len(unconfirmed)} UTXOs)",
        "",
        "Confirmed UTXO Details:",
    ]
    for u in sorted(confirmed, key=lambda x: x["value"], reverse=True):
        txid = u["txid"]
        lines.append(f"  {txid[:12]}…{txid[-6:]}:{u['vout']}  →  {u['value']:,} sats")

    lines.append(f"\nExplorer: {EXPLORER}/address/{addr}")
    return "\n".join(lines)


@mcp.tool()
def get_fee_rates() -> str:
    """
    Returns current network fee rates (sat/vB) for various priorities.
    """
    fees = _get("/v1/fees/recommended")
    if fees is None:
        return "Error: Unable to fetch network fee rates."

    fastest = fees.get("fastestFee", "?")
    half_hour = fees.get("halfHourFee", "?")
    one_hour = fees.get("hourFee", "?")
    economy = fees.get("economyFee", "?")
    minimum = fees.get("minimumFee", "?")

    return (
        f"Network Fees ({NETWORK.upper()}):\n"
        f"  Next Block      : {fastest} sat/vB\n"
        f"  ~30 min         : {half_hour} sat/vB\n"
        f"  ~1 hour         : {one_hour} sat/vB\n"
        f"  Economy         : {economy} sat/vB\n"
        f"  Min Relay       : {minimum} sat/vB"
    )


@mcp.tool()
def get_tx_status(txid: str) -> str:
    """
    Checks the status of a transaction (confirmed, mempool, not found).
    Args:
        txid: Transaction ID (64-char hex).
    """
    if len(txid) != 64 or not all(c in "0123456789abcdefABCDEF" for c in txid):
        return "Error: Invalid txid (must be 64 hex characters)."

    data = _get(f"/tx/{txid}")
    if data is None:
        return f"Transaction {txid[:16]}… not found in mempool or blockchain."

    status = data.get("status", {})
    confirmed = status.get("confirmed", False)
    block_height = status.get("block_height")

    fee = data.get("fee", "?")
    size = data.get("size", "?")
    weight = data.get("weight", "?")

    state = f"Confirmed at block #{block_height}" if confirmed else "Pending in mempool"

    return (
        f"Transaction : {txid}\n"
        f"Status      : {state}\n"
        f"Fee         : {fee} sats\n"
        f"Size        : {size} vB  ({weight} WU)\n"
        f"Explorer    : {EXPLORER}/tx/{txid}"
    )


@mcp.tool()
def get_governance_policy() -> str:
    """
    Retrieves the active governance policy sealed in the Enclave.
    """
    try:
        resp = _bridge.get_policy()
    except Exception as e:
        return f"Bridge error: {e}"

    if resp.get("type") == "Policy":
        p = resp.get("policy", {})
        allowance = p.get("allowance_sats", "?")
        version = p.get("version", "?")
        description = p.get("description", "?")
        whale = p.get("whale_policy", {}).get("type", "?")
        recovery = p.get("recovery_policy", {}).get("type", "?")
        script_hash = bytes(p.get("current_script_hash", [])).hex() or "?"

        return (
            f"Active Governance Policy:\n"
            f"  Description  : {description}\n"
            f"  Version      : {version}\n"
            f"  Allowance    : {allowance:,} sats\n"
            f"  Whale path   : {whale}\n"
            f"  Recovery path: {recovery}\n"
            f"  Script Hash  : {script_hash}"
        )

    err = resp.get("message") or resp.get("error") or json.dumps(resp)
    return f"Enclave error: {err}"


@mcp.tool()
def get_mempool_stats() -> str:
    """
    Returns global Bitcoin mempool statistics.
    """
    stats = _get("/mempool")
    if stats is None:
        return "Error: Mempool stats unavailable."

    count = stats.get("count", "?")
    vsize = stats.get("vsize", 0)
    total_fee = stats.get("total_fee", 0)

    return (
        f"Mempool ({NETWORK.upper()}):\n"
        f"  Total Pending Txs : {count:,}\n"
        f"  Total Size        : {vsize / 1_000_000:.2f} MvB\n"
        f"  Total Fees        : {total_fee:,} sats"
    )


# ── MCP Tools (OPERATIONS) ────────────────────────────────────────────────────

@mcp.tool()
def propose_batch_mint(rune_id: str, count: int, destination_address: str, fee_rate: Optional[int] = None) -> str:
    """
    Prepares a batch mint for a specific Rune.
    Generates the Master Mandate JSON for human signature.

    Args:
        rune_id: Full Rune ID (e.g., 894897:128).
        count: Number of mints (max 25).
        destination_address: Bitcoin address to receive Runes (bc1p...).
        fee_rate: Fee rate in sat/vB (uses 'hourFee' by default).
    """
    addr = _agent_address()
    if not addr:
        return "Error: Enclave unreachable."

    if not fee_rate:
        fees = _get("/v1/fees/recommended")
        fee_rate = fees.get("hourFee", 2) if fees else 2

    utxos = _get(f"/address/{addr}/utxo")
    if not utxos:
        return f"Error: Wallet empty. Send funds to: {addr}"

    # Estimate: Dust (546) + Protocol (1337) + Network (~160)
    cost_per_tx = 546 + 1337 + int(160 * fee_rate)
    total_required = cost_per_tx * count
    
    utxos = sorted(utxos, key=lambda x: x["value"], reverse=True)
    if utxos[0]["value"] < total_required:
        return f"Error: Insufficient budget. Required: {total_required} sats. Largest UTXO: {utxos[0]['value']} sats."

    # 3. Forge Manifest
    # protocol_address = The Agent (Spender)
    # destination_address = The Human (Receiver)
    batch_id = hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]
    manifest = OrderedDict([
        ("batch_id", batch_id),
        ("count", count),
        ("total_fee_sats", total_required),
        ("fee_rate", int(fee_rate)),
        ("rune_id", rune_id),
        ("protocol_address", addr),
        ("destination_address", destination_address)
    ])
    
    # Force single-line, no-whitespace JSON for deterministic hashing
    manifest_json = json.dumps(manifest, separators=(',', ':'))
    
    # Write to a file to prevent terminal wrapping corruption
    mandate_path = os.path.join(ROOT_DIR, "MANDATE.json")
    try:
        with open(mandate_path, "w") as f:
            f.write(manifest_json)
        logger.info(f"Mandate written to {mandate_path}")
    except Exception as e:
        logger.error(f"Failed to write mandate: {e}")

    instructions = (
        "📜 MASTER MANDATE GENERATED\n\n"
        "⚠️ TERMINAL DISPLAY MAY BE CORRUPTED BY WRAPPING.\n"
        f"👉 OPEN THE FILE: {mandate_path}\n"
        "Copy the content of that file (it's strictly one single line).\n\n"
        "SIGNING INSTRUCTIONS:\n"
        "1. Open Unisat or your wallet (Taproot account).\n"
        "2. Use 'Sign Message' tool.\n"
        "3. Paste the content of MANDATE.json exactly.\n"
        "4. Choose 'Sign by ecdsa'.\n"
        "5. Provide the signature to 'execute_batch_mint'."
    )
    return instructions


@mcp.tool()
def execute_batch_mint(manifest_json: str, mandate_signature: str) -> str:
    """
    Submits the signed mandate to the enclave to sign the batch transactions.
    """
    try:
        manifest = json.loads(manifest_json)
        addr = manifest.get("protocol_address")
        
        utxos = _get(f"/address/{addr}/utxo")
        if not utxos: return "Error: No UTXOs found."
        best = sorted(utxos, key=lambda x: x["value"], reverse=True)[0]
        
        bin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = f"cd {bin_dir}/enclave-signer && cargo run --release --bin gen_recovery_psbt -- {addr} {addr} 1000 {best['txid']}:{best['vout']}:{best['value']}"
        
        import subprocess
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if proc.returncode != 0:
            return f"Initial Forge failed: {proc.stderr}"
        
        initial_psbt = proc.stdout.strip()
        
        resp = _bridge.sign_batch_chain(
            psbt_base64=initial_psbt,
            batch_manifest=manifest,
            mandate_signature=mandate_signature
        )
        
        if resp.get("type") == "Error":
            return f"Enclave REJECTED batch: {resp.get('error')}"
            
        signed_txs = resp.get("signed_batch_psbts", [])
        
        res = [
            f"✅ BATCH AUTHORIZED: {len(signed_txs)} transactions signed.",
            "Use 'broadcast_transactions' to publish them."
        ]
        res.append("\nTransaction Hexes:")
        for i, tx in enumerate(signed_txs):
            res.append(f"TX {i+1}: {tx}")
            
        return "\n".join(res)
    except Exception as e:
        return f"Execution error: {e}"


@mcp.tool()
def broadcast_transactions(tx_hexes: List[str]) -> str:
    """
    Publishes a list of signed transactions to the Bitcoin network.
    """
    results = []
    for i, tx in enumerate(tx_hexes):
        try:
            r = requests.post(f"{API_BASE}/tx", data=tx, timeout=15)
            if r.status_code == 200:
                txid = r.text
                results.append(f"✅ TX {i+1} Published: {EXPLORER}/tx/{txid}")
                if i < len(tx_hexes) - 1:
                    time.sleep(2)
            else:
                results.append(f"❌ TX {i+1} FAILED: {r.text}")
                break
        except Exception as e:
            results.append(f"❌ TX {i+1} ERROR: {e}")
            break
            
    return "\n".join(results)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Sovereign Operations MCP Server...")
    try:
        mcp.run()
    except Exception as e:
        logger.critical(f"MCP Crash: {e}")
        sys.exit(1)

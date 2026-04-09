"""
agent_server.py — Sovereign Bitcoin Agent Gateway
Exposes /invoke endpoint. Implements a real tool-calling loop:
  Telegram → LLM (with tool schemas) → Tool execution (bridge.py) → Final response
  Support for Photo/QR JPG for governance updates.
"""

import os
import sys
import json
import logging
import asyncio
import httpx
import qrcode
import base64
import subprocess
import hashlib
from typing import List, Dict, Optional, Any
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from dotenv import load_dotenv

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# Load environment (Current and Parent)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Add mcp-bridge to path for direct tool execution
MCP_BRIDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "mcp-bridge")
if MCP_BRIDGE_DIR not in sys.path:
    sys.path.insert(0, MCP_BRIDGE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-15s | %(message)s"
)
logger = logging.getLogger("SovereignAgent")

# Context to store generated photo path during a request
current_photo_path = None

# Initialize Anthropic Client
client = None
if _ANTHROPIC_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY").strip().strip('"').strip("'"))

# ── Tool Registry ────────────────────────────────────────────────────────────
try:
    from bridge import EnclaveBridge
    import requests as _requests

    _bridge = EnclaveBridge()

    def _tool_get_bitcoin_address() -> str:
        resp = _bridge.get_policy()
        if resp.get("type") == "Error":
            return f"⚠️ Enclave offline: {resp.get('error')}. Start the Rust enclave first."
        addr = resp.get("address", "")
        if addr and "tb1p" in addr:
            return f"Your Mutinynet Taproot address: {addr}"
        return f"⚠️ No valid Mutinynet address found. Enclave may not be initialized. Raw: {resp}"

    def _tool_check_balance() -> str:
        addr_resp = _bridge.get_policy()
        address = addr_resp.get("address", "")
        # If enclave returns BalanceReport, it is ONLINE even if address is nested or missing.
        is_online = address or addr_resp.get("type") == "BalanceReport"
        if not is_online:
            return "Cannot fetch balance: Enclave offline or unauthorized."
        try:
            api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
            r = _requests.get(f"{api_url}/address/{address}/utxo", timeout=10)
            utxos = r.json() if r.status_code == 200 else []
            total = sum(u["value"] for u in utxos)
            return f"Balance: {total} sats ({len(utxos)} UTXOs) on Mutinynet. Address: {address}"
        except Exception as e:
            return f"Balance fetch error: {e}"

    def _tool_get_btc_price() -> str:
        """Fetches the current BTC price from the local UTXOracle state file."""
        # Default to a relative path in the parent directory
        default_oracle_path = os.path.join(os.path.dirname(__file__), "..", "btc_price_state.json")
        state_path = os.getenv("ORACLE_STATE_PATH", default_oracle_path)
        try:
            if not os.path.exists(state_path):
                return "🛡️ **UTXOracle Status:** Scanning L1... (Oracle state not found yet)"
                
            with open(state_path, "r") as f:
                state = json.load(f)
            price_cents = state.get("price_cents_uint64", 0)
            height = state.get("height", 0)
            price_usd = price_cents / 100.0
            return (f"🛡️ **UTXOracle Bitcoin Price:** ${price_usd:,.2f} USD\n"
                    f"⛏️ **Bitcoin Block:** {height}\n"
                    f"🔗 **Source:** Thermodynamic Proxy (Native L1)")
        except Exception as e:
            logger.error(f"Failed to read Oracle state: {e}")
            return f"UTXOracle state unreachable: {e}"

    def _tool_get_policy() -> str:
        resp = _bridge.get_policy()
        if resp.get("type") == "Policy":
            p_data = resp.get("policy", {})
            allowance = p_data.get('allowance_sats', 0)
            # Visual progress bar (conceptual)
            bar = "▓" * 5 + "░" * 15 
            return (
                f"🏛️ <b>GOVERNANCE PROTOCOL STATUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📜 <b>Description:</b> <code>{p.get('description')}</code>\n"
                f"🔢 <b>Version:</b> <code>v{p.get('version')}</code>\n"
                f"🔒 <b>Allowance:</b> <code>{allowance:,} sats</code>\n"
                f"📊 <code>{bar}</code>\n\n"
                f"🔑 <b>Active Taproot Multisig:</b>\n"
                f"<code>{resp.get('address', 'N/A')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
        return f"🚨 <b>Policy Error:</b> <code>{resp}</code>"

    def _get_fee_rate() -> float:
        """Fetches current fee rate from the appropriate Esplora endpoint.

        Network selection (BITCOIN_NETWORK env var):
          mainnet           → https://mempool.space/api/v1/fees/recommended
          signet/mutinynet  → https://mutinynet.com/api/v1/fees/recommended  (default)

        Returns a safe default of 1.2 sat/vB if the API is unreachable.
        """
        network = os.getenv("BITCOIN_NETWORK", "mutinynet").lower()
        if network == "mainnet":
            fee_url = "https://mempool.space/api/v1/fees/recommended"
        else:
            # Covers "mutinynet", "signet", "testnet", "regtest", and the absent case
            fee_url = "https://mutinynet.com/api/v1/fees/recommended"
        try:
            r = _requests.get(fee_url, timeout=5)
            # We use 'halfHourFee' + 20% premium for rapid confirmation
            base_fee = float(r.json().get("halfHourFee", 1.2))
            return round(base_fee * 1.2, 2)
        except:
            return 1.2

    def _tool_compose_transaction(payments: list, op_return_message: str = None, confirmed_high_fee: bool = False) -> str:
        """
        Constructs a complex Bitcoin transaction (multi-output, OP_RETURN).
        Enforces a 50 sat/vB safety trap.
        """
        fee_rate = _get_fee_rate()
        if fee_rate > 50 and not confirmed_high_fee:
            return (f"🚨 [FEE TRAP] Network congestion detected ({fee_rate} sat/vB). "
                    f"This complex transaction will be expensive. Please confirm by adding "
                    f"'confirmed_high_fee': true to your request.")

        # 1. Gather UTXO context (Same as old logic, fetching first UTXO)
        # 1. Gather UTXO context
        addr_resp = _bridge.get_policy()
        if addr_resp.get("type") == "error":
            return f"🚨 <b>Enclave Error:</b> <code>{addr_resp.get('message')}</code>"
            
        # Address may be nested in BalanceReport or missing in some versions
        address = addr_resp.get("address", "")
        if not address and addr_resp.get("type") == "BalanceReport":
             # Fallback: get_policy should return address, but if not, logic continues
             # for manual recovery if possible or reports specific missing data.
             pass

        if not address and addr_resp.get("type") != "BalanceReport":
            return "Error: Enclave offline or unauthorized."

        # 🛡️ ROOT FIX: Get scriptpubkey directly from the Enclave's authoritative response.
        # The Mutinynet Esplora API (/address/{addr}) does NOT return a 'scriptpubkey' field —
        # falling back to the stub "5120" produces a 2-byte invalid script that the Bit Machine
        # fails to match against the change output, causing it to count change as external spend.
        spk = addr_resp.get("script_pubkey_hex", "")

        try:
            api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
            r = _requests.get(f"{api_url}/address/{address}/utxo", timeout=10)
            utxos = r.json()
            if not utxos: return "Error: No UTXOs found in wallet."

            # Use the largest UTXO for the construction context
            best_utxo = max(utxos, key=lambda x: x["value"])

            # If the enclave didn't supply script_pubkey_hex (shouldn't happen), derive it
            # from the Esplora tx history as a last resort.
            if not spk:
                try:
                    txs_resp = _requests.get(f"{api_url}/address/{address}/txs", timeout=5)
                    for tx in txs_resp.json():
                        for vout in tx.get("vout", []):
                            if vout.get("scriptpubkey_address") == address:
                                spk = vout.get("scriptpubkey", "")
                                break
                        if spk:
                            break
                except Exception:
                    pass
            if not spk:
                logger.error("❌ [COMPOSE] Could not resolve scriptpubkey — aborting to prevent Bit Machine TRAP.")
                return "Error: Could not resolve wallet scriptpubkey. Enclave may be offline."

            # 2. Build JSON Request for Rust
            req = {
                "inputs": [{
                    "txid": best_utxo["txid"],
                    "vout": best_utxo["vout"],
                    "amount_sats": best_utxo["value"],
                    "script_pubkey_hex": spk
                }],
                "outputs": payments,
                "op_return_message": op_return_message,
                "fee_rate_sat_vb": fee_rate
            }
            
            b64_req = base64.b64encode(json.dumps(req).encode()).decode()
            
            # 3. Call Rust binary (Relative Discovery)
            binary_path = os.path.join(os.path.dirname(__file__), "..", "enclave-signer", "target", "release", "gen_live_psbt")
            if not os.path.exists(binary_path):
                # Fallback for debug environment
                binary_path = os.path.join(os.path.dirname(__file__), "..", "enclave-signer", "target", "debug", "gen_live_psbt")
            
            result = subprocess.run([binary_path, b64_req], capture_output=True, text=True)
            
            if result.returncode != 0:
                return f"Error forging PSBT: {result.stderr}"
            
            psbt_b64 = result.stdout.strip()
            
            # 4. Sign via Enclave
            sign_resp = _bridge.sign_transaction(psbt_b64)
            if sign_resp.get("type") == "Error":
                return f"Signing failed: {sign_resp.get('error')}"
            
            signed_tx = sign_resp.get("raw_hex")
            
            # 5. Broadcast (Instrumented)
            target_url = f"{api_url}/tx"
            logger.info(f"🚀 BROADCASTING TO: {target_url} (Hex: {signed_tx[:10]}...)")
            
            try:
                b_resp = _requests.post(target_url, data=signed_tx)
                if b_resp.status_code == 200:
                    txid = b_resp.text
                    return f"✅ Transaction Broadcasted!\nTXID: {txid}\nExplorer: https://mutinynet.com/tx/{txid}"
                else:
                    err_msg = b_resp.text
                    logger.warning(f"❌ Primary Broadcast Failed: {err_msg}")
                    # Fallback to system curl if we detect RPC error formatting
                    if "RPC error" in err_msg or "code\":-25" in err_msg:
                        logger.info("🛡️  RPC Error detected. Attempting Sovereign Bypass via direct curl...")
                        # 🛡️ Sovereign Bypass: Direct CURL to Mutinynet if RPC is too strict
                        curl_cmd = f'curl -s -X POST "{api_url}/tx" -d "{signed_tx}"'
                        try:
                            result = subprocess.check_output(curl_cmd, shell=True).decode().strip()
                            if len(result) == 64:
                                return f"✅ Transaction Broadcasted via Sovereign Bypass: {result}"
                            
                            # Check for burn restriction
                            if "maxburnamount" in result.lower():
                                logger.warning("🚨 Network policy forbids OP_RETURN (maxburnamount). Recommendation: Try without message.")
                                return f"❌ Rejected by Network: The node forbids etched messages (maxburnamount). Please try the transaction without a message."
                            
                            return f"Broadcast failed (Bypass Mode): {result}"
                        except Exception as e:
                            return f"Sovereign Bypass Failure: {e}"
                    return f"Broadcast failed: {err_msg}"
            except Exception as e:
                return f"Broadcast exception: {e}"

        except Exception as e:
            return f"Transaction assembly failed: {e}"

    def _tool_propose_policy_update(new_allowance_sats: int) -> str:
        """Generates a JPG QR code for the user to sign/authorize a policy update."""
        global current_photo_path
        logger.info(f"🚨 [GOVERNANCE] Proposing update: allowance = {new_allowance_sats} sats")
        # Mock Gov PSBT (same as in mcp-bridge/server.py)
        gov_psbt_mock = "cHNidP8BAF4CAAAAAa2Xi3LuygT1I9xQr8vqKsJjhaLEFtLwIffZP9jCqehVAQAAAAD/////ATAbDwAAAAAAIlEgpDGQqNn09SbymYZ0pUWpxeGs9iksg0Un59+u43Y4StAAAAAAAAAA"
        qr_data = f"bitcoin:?psbt={gov_psbt_mock}"
        photo_path = "/tmp/policy_update_qr.jpg"

        try:
            img = qrcode.make(qr_data)
            img.save(photo_path)
            current_photo_path = photo_path
            return f"Governance proposal for {new_allowance_sats} sats created. Please scan the attached QR code with your hardware wallet or signer to authorize."
        except Exception as e:
            return f"Failed to generate QR: {e}"

    # ── mint_rune ─────────────────────────────────────────────────────────────
    def _tool_mint_rune(
        rune_name: Optional[str] = None,
        rune_id: Optional[str] = None,
        amount: int = 1,
        divisibility: int = 0,
        symbol: Optional[str] = None,
        open_mint: bool = True,
        destination_address: Optional[str] = None,
        payments: Optional[list] = None
    ) -> str:
        """
          output[N+1]  change → enclave address         (if any)
          output[last] 0-sat Runestone OP_RETURN OP_13 (built by Rust forge)
        """
        logger.info(f"🪙  [FORGE] mint_rune called: name={rune_name!r} amount={amount} extra_payments={len(payments or [])}")

        # ── 1. Validate inputs ────────────────────────────────────────────────
        if not rune_name and not rune_id:
            return "⚠️ Must provide either a 'rune_name' to etch or a 'rune_id' to mint."

        if rune_name:
            import re as _re
            if not _re.match(r'^[A-Z•]+$', rune_name) or rune_name.startswith('•') or rune_name.endswith('•'):
                logger.warning(f"❌ [FORGE] Invalid Rune name: {rune_name!r}")
                return (
                    f"⚠️ Invalid Rune name '{rune_name}'. "
                    "Names must be uppercase A–Z with optional '•' spacers. No leading/trailing spacers."
                )

        # ── 2. Validate payments ──────────────────────────────────────────────
        total_payment_sats = 0
        if payments:
            for p in payments:
                if "address" not in p or "amount_sats" not in p:
                    return "⚠️ Invalid payment structure within 'payments' list."
                total_payment_sats += p["amount_sats"]

        # ── 3. Resolve block height & parameters ─────────────────────────────
        network = os.getenv("BITCOIN_NETWORK", "mutinynet").lower()
        api_url = os.getenv("MAINNET_API_URL", "https://mempool.space/api") if network == "mainnet" else os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
        
        current_height = None
        try:
            h_resp = _requests.get(f"{api_url}/blocks/tip/height", timeout=5)
            if h_resp.status_code == 200:
                current_height = int(h_resp.text.strip())
                logger.info(f"⛏️  [FORGE] Current block height resolved: {current_height}")
        except Exception as e:
            logger.warning(f"⚠️ [FORGE] Failed to fetch current height: {e}. Enclave may reject short names.")

        # Resolve enclave address
        addr_resp = _bridge.get_policy()
        if addr_resp.get("type") == "Error":
            return f"⚠️ Enclave offline: {addr_resp.get('error')}."

        address = addr_resp.get("address", "")
        spk = addr_resp.get("script_pubkey_hex", "")
        if not address:
            return "⚠️ Could not resolve enclave address."

        # ── 4. Fetch UTXOs and check minimum balance ───────────────────────────
        RUNE_DUST_SATS = 546
        ESTIMATED_FEE = 2000
        MINIMUM_REQUIRED = RUNE_DUST_SATS + total_payment_sats + ESTIMATED_FEE

        try:
            api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
            r = _requests.get(f"{api_url}/address/{address}/utxo", timeout=10)
            utxos = r.json() if r.status_code == 200 else []
        except Exception as e:
            return f"⚠️ Balance fetch failed: {e}"

        total_balance = sum(u["value"] for u in utxos)
        if total_balance < MINIMUM_REQUIRED:
            return f"⚠️ Insufficient balance. Needs ~{MINIMUM_REQUIRED:,} sats, has {total_balance:,} sats."

        best_utxo = max(utxos, key=lambda x: x["value"])

        # ── 5. Resolver Logic (Defaults to OP•RETURN•WAR) ─────────────────────
        # If no arguments provided, we prioritize the sovereign baptism rune.
        final_rune_id = rune_id
        final_rune_name = rune_name

        if not final_rune_id and not final_rune_name:
            final_rune_id = "894897:128"
            logger.info("ℹ️  [FORGE] No Rune target provided. Defaulting to OP•RETURN•WAR (894897:128)")

        fee_rate = _get_fee_rate()
        rune_mint_params = {
            "amount": amount,
            "divisibility": divisibility,
            "open_mint": open_mint,
            "recipient_output": 1,
            "current_height": current_height,
        }
        if final_rune_name:
            rune_mint_params["rune_name"] = final_rune_name
        if final_rune_id:
            rune_mint_params["mint_id"] = final_rune_id
        if symbol is not None:
            rune_mint_params["symbol"] = symbol

        # 6. Final output list: [Dust, ...Payments]
        # Dust (vout[1]) goes to destination_address if provided, else Enclave address.
        recipient = destination_address or address
        combined_outputs = [{"address": recipient, "amount_sats": RUNE_DUST_SATS}]
        if payments:
            combined_outputs.extend(payments)

        req = {
            "inputs": [{
                "txid": best_utxo["txid"],
                "vout": best_utxo["vout"],
                "amount_sats": best_utxo["value"],
                "script_pubkey_hex": spk,
            }],
            "outputs": combined_outputs,
            "op_return_message": None,
            "fee_rate_sat_vb": fee_rate,
            "rune_mint": rune_mint_params,
        }

        b64_req = base64.b64encode(json.dumps(req).encode()).decode()

        # ── 7. Build PSBT via Rust binary ─────────────────────────────────────
        binary_path = os.path.join(
            os.path.dirname(__file__), "..", "enclave-signer",
            "target", "release", "gen_live_psbt"
        )
        if not os.path.exists(binary_path):
            binary_path = os.path.join(
                os.path.dirname(__file__), "..", "enclave-signer",
                "target", "debug", "gen_live_psbt"
            )

        result = subprocess.run([binary_path, b64_req], capture_output=True, text=True)
        if result.returncode != 0:
            return f"⚠️ PSBT construction failed: {result.stderr}"

        psbt_b64 = result.stdout.strip()
        logger.info(f"🪙  [FORGE] PSBT built, sending to enclave for signing...")

        # ── 8. Sign via Enclave ───────────────────────────────────────────────
        sign_resp = _bridge.sign_transaction(psbt_b64)
        if sign_resp.get("type") == "Error":
            return f"⚠️ Enclave signing failed: {sign_resp.get('error')}"

        signed_tx = sign_resp.get("raw_hex")

        # ── 9. Broadcast ──────────────────────────────────────────────────────
        try:
            b_resp = _requests.post(f"{api_url}/tx", data=signed_tx)
            if b_resp.status_code == 200:
                txid = b_resp.text.strip()
                logger.info(f"✅ [FORGE] Rune etched! TXID: {txid}")
                return (
                    f"🪙 <b>RUNE OPERATION SUCCESSFUL</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Rune Name  : <code>{rune_name or 'Existing Rune'}</code>\n"
                    f"Rune ID    : <code>{rune_id or 'New Etch'}</code>\n"
                    f"Action     : <code>{'Minting' if rune_id else 'Etching'}</code>\n"
                    f"Supply     : <code>{amount:,}</code> "
                    f"(divisibility: {divisibility})\n"
                    f"Payments   : <code>{len(payments or [])} extra</code>\n"
                    f"Protocol   : OP_RETURN OP_13 (Runes)\n"
                    f"Recipient  : <code>{recipient}</code> (Output Index 1)\n"
                    f"TXID       : <code>{txid}</code>\n"
                    f"Explorer   : https://mutinynet.com/tx/{txid}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
            else:
                logger.warning(f"❌ [FORGE] Broadcast failed: {b_resp.text}")
                return f"⚠️ Broadcast failed: {b_resp.text}"
        except Exception as e:
            return f"⚠️ Broadcast exception: {e}"
            
    def _tool_batch_mint_rune(
        rune_id: str,
        count: int = 25,
        master_signature: str = None,
        batch_id: str = None
    ) -> str:
        """Executes a Daisy-Chain batch of minting transactions authorized by master mandate."""
        logger.info(f"⛓️ [BATCH] batch_mint_rune called for {rune_id} (count={count})")

        # ── 1. Resolve Enclave Context ────────────────────────────────────────
        addr_resp = _bridge.get_policy()
        if addr_resp.get("type") == "Error":
            return f"⚠️ Enclave offline: {addr_resp.get('error')}."
        address = addr_resp.get("address", "")
        spk = addr_resp.get("script_pubkey_hex", "")
        if not address: return "⚠️ Could not resolve enclave address."

        # ── 2. Balance Check ──────────────────────────────────────────────────
        # Each tx spends ~3883 sats. 25 txs ~ 97,075 sats.
        PER_TX_COST = 546 + 1337 + 2000
        TOTAL_COST = PER_TX_COST * count
        
        try:
            api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
            r = _requests.get(f"{api_url}/address/{address}/utxo", timeout=10)
            utxos = r.json() if r.status_code == 200 else []
            total_balance = sum(u["value"] for u in utxos)
            if total_balance < TOTAL_COST:
                return f"⚠️ Insufficient balance for batch. Needs ~{TOTAL_COST:,} sats, has {total_balance:,} sats."
            best_utxo = max(utxos, key=lambda x: x["value"])
        except Exception as e:
            return f"⚠️ UTXO fetch failed: {e}"

        # ── 3. Build Initial PSBT (Context ONLY) ──────────────────────────────
        # We build a dummy PSBT just to pass the input context to the enclave.
        req = {
            "inputs": [{
                "txid": best_utxo["txid"],
                "vout": best_utxo["vout"],
                "amount_sats": best_utxo["value"],
                "script_pubkey_hex": spk,
            }],
            "outputs": [{"address": address, "amount_sats": 546}], # Dummy output
            "op_return_message": None,
            "fee_rate_sat_vb": 1.0,
            "rune_mint": None,
        }
        b64_req = base64.b64encode(json.dumps(req).encode()).decode()
        
        binary_path = os.path.join(os.path.dirname(__file__), "..", "enclave-signer", "target", "release", "gen_live_psbt")
        if not os.path.exists(binary_path):
            binary_path = os.path.join(os.path.dirname(__file__), "..", "enclave-signer", "target", "debug", "gen_live_psbt")
        
        result = subprocess.run([binary_path, b64_req], capture_output=True, text=True)
        if result.returncode != 0: return f"⚠️ PSBT build failed: {result.stderr}"
        psbt_b64 = result.stdout.strip()

        # ── 4. Verify/Construct Manifest ──────────────────────────────────────
        # protocol_address is where the Dust goes (Master Human address)
        manifest = {
            "batch_id": batch_id or f"batch-{int(asyncio.get_event_loop().time())}",
            "count": count,
            "total_fee_sats": TOTAL_COST,
            "rune_id": rune_id,
            "protocol_address": address, # Master Human destination
        }

        # ── 5. ASYNCHRONOUS MANDATE HANDSHAKE ─────────────────────────────────
        if not master_signature:
            manifest_json = json.dumps(manifest, separators=(',', ':'))
            import hashlib
            manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
            
            logger.info(f"🛡️ [BATCH] Signature missing. Returning Mandate Proposal for {manifest['batch_id']}.")
            
            return (
                f"🛡️ <b>MANDATE PROPOSAL: {manifest['batch_id']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"The Agent is ready to forge a <b>{count}-transaction</b> chain, "
                f"but requires a cryptographic signature to bypass autonomous allowance limits.\n\n"
                f"<b>MANIFEST (JSON):</b>\n"
                f"<code>{manifest_json}</code>\n\n"
                f"<b>SHA-256 HASH:</b>\n"
                f"<code>{manifest_hash}</code>\n\n"
                f"<b>ACTION REQUIRED:</b>\n"
                f"1. Copy the JSON manifest above.\n"
                f"2. Sign it using your <b>Master Key</b> (Schnorr BIP-340).\n"
                f"3. Provide the resulting signature hex to the <code>batch_mint_rune</code> tool.\n\n"
                f"<b>SIGNING HELPER:</b>\n"
                f"Run this command locally to generate the signature:\n"
                f"<code>python3 scripts/sign_mandate.py '{manifest_json}' YOUR_PRIVATE_KEY</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>State: PENDING_SIGNATURE</i>"
            )

        # ── 6. Call Enclave for Batch Signing ─────────────────────────────────
        logger.info(f"🛡️ [BATCH] Signature provided. Sending mandate to Enclave for signing...")
        sign_resp = _bridge.sign_batch_chain(psbt_b64, manifest, master_signature)
        if sign_resp.get("type") == "Error":
            return f"❌ <b>MANDATE REJECTED</b>\n\nThe Enclave refused to sign the batch.\nError: <code>{sign_resp.get('error')}</code>\n\nCheck that the signature matches the manifest and the MASTER_HUMAN_PUBKEY stored in the enclave."

        signed_txs = sign_resp.get("signed_batch_psbts", [])
        if not signed_txs:
            return "⚠️ Enclave returned no signed transactions for the batch."

        # ── 6. Sequential Broadcast Loop ──────────────────────────────────────
        logger.info(f"🚀 [BATCH] Enclave signed {len(signed_txs)} transactions. Starting broadcast loop...")
        txids = []
        
        for i, raw_hex in enumerate(signed_txs):
            try:
                b_resp = _requests.post(f"{api_url}/tx", data=raw_hex)
                if b_resp.status_code == 200:
                    txid = b_resp.text.strip()
                    txids.append(txid)
                    logger.info(f"✅ [BATCH] Tx {i+1}/{len(signed_txs)} broadcasted: {txid}")
                else:
                    logger.error(f"❌ [BATCH] Tx {i+1} FAILED: {b_resp.text}")
                    return f"⚠️ Batch broken at step {i+1}: {b_resp.text}. {len(txids)} txs succeeded."
            except Exception as e:
                return f"⚠️ Batch broadcast exception at step {i+1}: {e}"

        return (
            f"🚀 <b>DAISY-CHAIN BATCH SUCCESSFUL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Batch ID   : <code>{manifest['batch_id']}</code>\n"
            f"Rune ID    : <code>{rune_id}</code>\n"
            f"Count      : <code>{len(txids)} / {count}</code>\n"
            f"Status     : ⛓️ Linked & Broadcasted\n"
            f"First TXID : <code>{txids[0]}</code>\n"
            f"Last TXID  : <code>{txids[-1]}</code>\n"
            f"Explorer   : https://mutinynet.com/address/{address}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

except Exception as e:
    logger.warning(f"⚠️  Wallet tools unavailable: {e}")

def _tool_get_governance_policy() -> str:
    """Returns the current allowance and version from the Enclave."""
    try:
        resp = _bridge.get_policy()
        p = resp.get('policy', {})
        return f"Policy v{p.get('version', 0)}: Allowance {p.get('allowance_sats', 0):,} sats. Status: {p.get('status', 'Active')}"
    except Exception as e:
        return f"Error fetching policy: {e}"

# Tool dispatch
TOOL_DISPATCH = {
    "get_my_bitcoin_address":  _bridge.get_address,
    "check_my_balance":        _tool_check_balance,
    "get_bitcoin_price":       _tool_get_btc_price,
    "get_governance_policy":   _tool_get_governance_policy,
    "compose_transaction":     _tool_compose_transaction,
    "propose_policy_update":   _tool_propose_policy_update,
    "mint_rune":               _tool_mint_rune,
    "batch_mint_rune":         _tool_batch_mint_rune,
}

TOOL_SCHEMAS = [
    {"name": "get_my_bitcoin_address", "description": "Returns the sovereign Taproot address.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_my_balance", "description": "Fetches the live sats balance.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_bitcoin_price", "description": "Fetches the real-time Bitcoin price from UTXOracle.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_governance_policy", "description": "Returns the current allowance sealed in the Enclave.", "input_schema": {"type": "object", "properties": {}}},
    {
        "name": "compose_transaction", 
        "description": "Constructs a Bitcoin transaction (multi-output batching or simple transfer) with elective OP_RETURN message. Has a 50 sat/vB safety trap.",
        "input_schema": {
            "type": "object", 
            "properties": {
                "payments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "address": {"type": "string"},
                            "amount_sats": {"type": "integer"}
                        },
                        "required": ["address", "amount_sats"]
                    },
                    "description": "List of destination addresses and amounts (max 10 outputs)."
                },
                "op_return_message": {
                    "type": "string", 
                    "maxLength": 80, 
                    "description": "Immutable message to etch into the blockchain (max 80 bytes)."
                },
                "confirmed_high_fee": {
                    "type": "boolean",
                    "description": "Set to true only if the user confirmed executing despite network congestion (>50 sat/vB)."
                }
            },
            "required": ["payments"]
        }
    },
    {
        "name": "propose_policy_update",
        "description": "Calls this UNLESS a transfer is within allowance. Generates a QR code for the user to authorize a higher limit.",
        "input_schema": {
            "type": "object",
            "properties": {"new_allowance_sats": {"type": "integer"}},
            "required": ["new_allowance_sats"]
        }
    },
    {
        "name": "mint_rune",
        "description": (
            "Etches a new Rune (Bitcoin fungible token) onto L1 using the Runes protocol "
            "(OP_RETURN OP_13). Constructs a Runestone inside the TEE enclave — no ord CLI. "
            "Use when the user says 'mint a rune', 'etch TOKEN_NAME', or 'create rune token'. "
            "The standard flagship Rune for testing on Mutinynet is 'OP•RETURN•WAR'. "
            "Always call check_my_balance first to verify sufficient funds (~2000 sats minimum)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rune_name": {
                    "type": "string",
                    "description": (
                        "Canonical Rune name: uppercase A–Z with optional bullet spacers •. "
                        "Examples: 'SOVEREIGN', 'BITCOIN•PIZZA', 'UNCOMMON•GOODS'. "
                        "No lowercase, no digits, no leading/trailing spacers."
                    )
                },
                "rune_id": {
                    "type": "string",
                    "description": "Rune ID in 'block:tx' format (e.g. '123456:7'). Use this to MINT an existing Rune."
                },
                "amount": {
                    "type": "integer",
                    "description": "Total supply to premine (Etch) or units to generate (Mint). Default: 1000."
                },
                "divisibility": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 38,
                    "description": "Number of decimal places (Etch only). Default: 0."
                },
                "symbol": {
                    "type": "string",
                    "maxLength": 1,
                    "description": "Unicode ticker symbol (Etch only). Optional."
                },
                "open_mint": {
                    "type": "boolean",
                    "description": "If true, etches the Rune with open minting terms allowing others to mint supply. (Etch only)."
                },
                "payments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "address": {"type": "string"},
                            "amount_sats": {"type": "integer"}
                        },
                        "required": ["address", "amount_sats"]
                    },
                    "description": "Optional payment outputs to include in the same transaction (e.g., sending sats to a partner while etching)."
                }
            },
            "required": ["rune_name", "amount"]
        }
    },
    {
        "name": "batch_mint_rune",
        "description": (
            "Executes an authorized DAISY-CHAIN batch of 25 minting transactions. "
            "Requires a valid Master Mandate signature to bypass autonomous allowances. "
            "Use this ONLY for heavy-duty accumulation of existing Runes like 'OP•RETURN•WAR'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rune_id": {"type": "string", "description": "Rune ID: 'block:tx' (e.g. '894897:128')."},
                "count": {"type": "integer", "description": "Number of transactions in the chain (max 25).", "default": 25},
                "master_signature": {"type": "string", "description": "The Schnorr signature from the Master Human key authorizing the BatchManifest JSON."},
                "batch_id": {"type": "string", "description": "Unique identifier for this batch (must match signed mandate)."}
            },
            "required": ["rune_id", "master_signature", "batch_id"]
        }
    }
]

OLLAMA_TOOL_SCHEMAS = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in TOOL_SCHEMAS]

# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(title="Sovereign Bitcoin Agent API", version="2.1.0")

SYSTEM_PROMPT = """You are the Sovereign Bitcoin Agent — an elite AI managing a real Bitcoin wallet on Mutinynet.

### CRITICAL INSTRUCTION:
- ALWAYS extract Bitcoin Taproot addresses (starting with 'tb1p') and amounts (in sats) from the user's message.
- DO NOT ask for information that is already provided in the prompt.
- If multiple addresses and amounts are provided, batch them into a single 'compose_transaction' call (max 10).
- If a transfer exceeds your allowance, immediately call 'propose_policy_update'.

### OPERATIONAL RULES:
- Respond in English only.
- Use the [ENCLAVE REALITY STATUS] below as your source of truth for your own address and limits.

### RECEIPT FORMAT (MANDATORY on success):
After every successful transaction, you MUST end your response with this exact block — no variations:

<<<RECEIPT>>>
TXID: <the exact 64-character transaction id>
OUTPUTS: <address1>=<amount1>, <address2>=<amount2>
OP_RETURN: <exact message text, or NONE>
BALANCE: <remaining balance in sats>
<<<END>>>
"""

class InvokeRequest(BaseModel):
    prompt: str
    model: str = "claude-3-5-sonnet-20241022"

class InvokeResponse(BaseModel):
    reply: str
    provider: str
    model: str
    photo: Optional[str] = None

def _execute_tool(name: str, inputs: dict) -> str:
    fn = TOOL_DISPATCH.get(name)
    if fn:
        logger.info(f"🔧 Executing tool: {name} (args: {inputs})")
        if name == "compose_transaction":
            return fn(inputs.get("payments"), inputs.get("op_return_message"), inputs.get("confirmed_high_fee", False))
        if name == "propose_policy_update":
            return fn(inputs.get("new_allowance_sats"))
        if name == "mint_rune":
            return fn(
                inputs.get("rune_name"), inputs.get("rune_id"), inputs.get("amount", 1000),
                inputs.get("divisibility", 0), inputs.get("symbol"), inputs.get("open_mint", False),
                inputs.get("destination_address"), inputs.get("payments")
            )
        if name == "batch_mint_rune":
            return fn(
                inputs.get("rune_id"), inputs.get("count", 25), 
                inputs.get("master_signature"), inputs.get("batch_id")
            )
        return fn()
    return f"Unknown tool: {name}"

async def _invoke_anthropic(prompt: str, context_prompt: str) -> InvokeResponse:
    global current_photo_path
    current_photo_path = None
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY missing in .env")
        
    messages = [{"role": "user", "content": prompt}]
    
    # Simple tool transformation for Anthropic
    anthropic_tools = []
    for t in TOOL_SCHEMAS:
        anthropic_tools.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"]
        })
    try:
        for _ in range(5):
            # Anthropic requires specific tool format
            response = await client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2048,
                system=context_prompt,
                tools=anthropic_tools,
                messages=messages
            )
            
            stop_reason = response.stop_reason
            content = response.content
            
            if stop_reason == "end_turn":
                reply = ""
                for block in content:
                    if block.type == "text":
                        reply += block.text
                return InvokeResponse(reply=reply or "Done.", provider="anthropic", model="claude-3-5-sonnet-20241022", photo=current_photo_path)
            
            if stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": content})
                tool_results = []
                for block in content:
                    if block.type == "tool_use":
                        res = _execute_tool(block.name, block.input)
                        logger.info(f"🔧 Tool result: {res}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": res
                        })
                messages.append({"role": "user", "content": tool_results})
        return InvokeResponse(reply="Max turns reached.", provider="anthropic", model="claude-3-5-sonnet-20241022")
    finally:
        pass

async def _invoke_ollama(prompt: str, context_prompt: str) -> InvokeResponse:
    """Invokes Ollama/Qwen with tool support."""
    model = os.getenv("OLLAMA_BIG_MODEL", "hermes3")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    messages = [{"role": "system", "content": context_prompt}, {"role": "user", "content": prompt}]
    
    client = httpx.AsyncClient(timeout=180.0)
    try:
        for _ in range(5):
            resp = await client.post(
                f"{base_url}/api/chat",
                json={"model": model, "messages": messages, "tools": OLLAMA_TOOL_SCHEMAS, "stream": False}
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Ollama error: {resp.text}")
                
            data = resp.json()
            msg = data.get("message", {})
            tool_calls = msg.get("tool_calls", [])
            
            if not tool_calls:
                content = msg.get("content", "")
                if not content:
                    return InvokeResponse(reply="Decision processing complete.", provider="ollama", model=model, photo=current_photo_path)
                logger.info(f"🧠 Ollama response (TEXT ONLY): {content[:100]}...")
                clean_lines = [l for l in content.splitlines() if not l.strip().startswith('[{"name')]
                return InvokeResponse(reply="\n".join(clean_lines).strip() or "Processed.", provider="ollama", model=model, photo=current_photo_path)
                
            # If tool calls present, Ollama needs content as string, not None
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                res = _execute_tool(tool_name, fn.get("arguments", {}))
                logger.info(f"🔧 Tool result: {res}")
                # Maintain tool link for model continuity
                messages.append({"role": "tool", "name": tool_name, "content": res})
            messages.append({"role": "user", "content": "Use the tool results above to proceed with the transaction if allowed."})
        return InvokeResponse(reply="Max turns reached.", provider="ollama", model=model)
    finally:
        await client.aclose()

@app.post("/invoke", response_model=InvokeResponse)
async def invoke_agent(req: InvokeRequest):
    """
    Sovereign Agent Gateway. 
    Now with Pre-emptive Enclave Context Injection to eliminate LLM amnesia.
    """
    prompt = req.prompt
    logger.info(f"📩 Processing order (LEN={len(prompt)}): {repr(prompt)}")

    # 🔎 Step -1: Pre-emptive Regex Scan for Taproot Addresses (Sensory Augmentation)
    import re
    detected_addresses = re.findall(r"(tb1p[a-zA-Z0-9]{39,})", prompt, re.IGNORECASE)
    address_hint = ""
    if detected_addresses:
        address_hint = f"\n- [SENSORY HINT] Detected potential destination addresses: {', '.join(detected_addresses)}"
        logger.info(f"🔎 Detected {len(detected_addresses)} addresses in prompt.")
    else:
        logger.warning("❌ Sensory failure: No Taproot addresses detected by Regex scanner.")

    # 🛡️ Step 0: Pre-fetch Enclave Reality to avoid amnesia
    try:
        policy_resp = _bridge.get_policy()
        policy = policy_resp.get('policy', {}) or {}
        balance_data = _tool_check_balance()
        enclave_context = f"""
        [ENCLAVE REALITY STATUS]
        - Taproot Address: {policy_resp.get('address')}
        - Current Allowance: {policy.get('allowance_sats', 0)} sats
        - Available Balance: {balance_data}{address_hint}
        """
    except Exception as e:
        enclave_context = "[ENCLAVE STATUS: UNREACHABLE]"
        logger.error(f"❌ Enclave context pre-fetch failed: {e}")

    # 🧠 Augmented System Prompt with Real-time Context
    CONTEXT_PROMPT = f"""
    {SYSTEM_PROMPT}

    {enclave_context}

    IMPORTANT: If addresses are mentioned in the conversation or the Sensory Hint, call 'compose_transaction' immediately. Reply in English only.
    """

    # 🚀 Sensory-First User Prompt Augmentation
    augmented_prompt = prompt
    if address_hint:
        augmented_prompt = f"{prompt}\n\n[SYSTEM NOTIFICATION: {address_hint.strip()}. Use these addresses to perform the requested transaction now.]"
        logger.info("🚀 Augmented user prompt with sensory discovery.")
    
    # Debug: Log the incoming request to ensure full transparency
    logger.info(f"🧪 FORWARDING TO LLM: {augmented_prompt}")

    try:
        # 🧪 Primary: Claude 3.5 Sonnet
        key = os.getenv("ANTHROPIC_API_KEY")
        if _ANTHROPIC_AVAILABLE and key:
            try:
                return await _invoke_anthropic(augmented_prompt, CONTEXT_PROMPT)
            except Exception as e:
                # Capture the full error detail if available from Anthropic
                error_msg = str(e)
                if hasattr(e, 'response') and hasattr(e.response, 'text'):
                    error_msg = f"{e} | DETAIL: {e.response.text}"
                logger.error(f"⚠️ Claude Exception: {error_msg}")
                # Falling back to Ollama

        # 🦀 Fallback/Main: Local Ollama (Resilient Mode)
        return await _invoke_ollama(augmented_prompt, CONTEXT_PROMPT)
    except Exception as e:
        logger.error(f"🚨 Master Agent Failure: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # 🌍 Universal binding to ensure Oracle connectivity
    uvicorn.run(app, host="0.0.0.0", port=8000)

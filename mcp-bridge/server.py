import os
import sys
import base64
import json
import logging
import requests
import qrcode
import subprocess
import webbrowser

# --- PURE SOVEREIGN LOGGING ---
# We log to stderr to avoid corrupting the MCP stdio (stdout) stream
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("SovereignMCP")
logger.info("🛡️ [STARTUP] Initializing Sovereign Bitcoin MCP Server...")

# Ensure the bridge module can be found regardless of how we are called
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
if BRIDGE_DIR not in sys.path:
    sys.path.append(BRIDGE_DIR)
    logger.info(f"⚙️ [PATH] Added {BRIDGE_DIR} to sys.path")

try:
    from mcp.server.fastmcp import FastMCP
    from bridge import EnclaveBridge
    logger.info("✅ [DOCS] Dependencies and Bridge module loaded successfully.")
except Exception as e:
    logger.error(f"❌ [CRITICAL] Failed to load dependencies: {e}")
    sys.exit(1)

# Initialize FastMCP server
mcp = FastMCP("Sovereign Bitcoin Wallet")
bridge = EnclaveBridge()

@mcp.tool()
def get_my_bitcoin_address() -> str:
    """Returns the wallet's real Taproot (P2TR) address for Mutinynet."""
    try:
        resp = bridge.get_policy()
        addr = resp.get("address")
        if not addr or "tb1p" not in addr:
            return "Error: Enclave returned an invalid address."
        return addr
    except Exception as e:
        logger.error(f"Error in get_my_bitcoin_address: {e}")
        return f"Error: {e}"

@mcp.tool()
def get_my_script_pubkey() -> str:
    """Returns the wallet's real hex-encoded scriptPubKey for Taproot."""
    try:
        resp = bridge.get_policy()
        spk = resp.get("script_pubkey_hex")
        if not spk or "5120" not in spk:
            return "Error: Enclave returned an invalid scriptPubKey."
        return spk
    except Exception as e:
        logger.error(f"Error in get_my_script_pubkey: {e}")
        return f"Error: {e}"

@mcp.tool()
def check_my_balance() -> str:
    """Fetches the live balance from Mutinynet Signet API."""
    address = get_my_bitcoin_address()
    if "Error" in address: return address
    try:
        api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
        response = requests.get(f"{api_url}/address/{address}/utxo")
        if response.status_code != 200:
            return f"API Error: {response.status_code}"
        utxos = response.json()
        total_sats = sum(utxo['value'] for utxo in utxos)
        return f"Live Balance: {total_sats} sats on Mutinynet."
    except Exception as e:
        return f"API Error: {e}"

@mcp.tool()
def send_bitcoin(to_address: str, amount_sats: int, provided_signatures: list[str] = None) -> str:
    """Sends bitcoin via the Enclave policy engine."""
    try:
        # 1. 🛡️ REAL UTXO FETCH: Ensure we use a broadcastable input
        address_str = get_my_bitcoin_address()
        api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
        utxo_res = requests.get(f"{api_url}/address/{address_str}/utxo")
        utxos = utxo_res.json() if utxo_res.status_code == 200 else []
        
        # 🛡️ HARDENED UTXO SELECTION: Prefer confirmed, highest value first
        confirmed_utxos = [u for u in utxos if u.get("status", {}).get("confirmed")]
        if not confirmed_utxos and utxos:
            logger.warning("⚙️ [UTXO] No confirmed UTXOs found. Attempting to use unconfirmed...")
            confirmed_utxos = utxos
            
        if not confirmed_utxos:
            logger.error("❌ [UTXO] No UTXOs available to spend.")
            return "Error: No UTXOs found on Mutinynet for your address."

        # Sort by value DESC to maximize success
        confirmed_utxos.sort(key=lambda x: x.get("value", 0), reverse=True)
        real_utxo = confirmed_utxos[0]
        
        # 🛡️ FEE ESTIMATION: Fetch from network
        try:
            api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
            fee_res = requests.get(f"{api_url}/fee-estimates")
            fees = fee_res.json()
            fee_rate = fees.get("2", 1.5)
            logger.info(f"⚙️ [FEE] Network Estimate (2 blocks): {fee_rate} sat/vB")
        except:
            fee_rate = 1.1
            logger.warning("⚙️ [FEE] API failed, using fallback 1.1 sat/vB")

        # 🛡️ CONTEXT DELEGATION & GENERATION (Phase 26: JSON-Base64 Protocol)
        real_script_hex = get_my_script_pubkey()
        if "Error" in real_script_hex: return real_script_hex
        
        # 🛡️ BALANCE SAFETY CHECK: Detect NULL/Burn scripts
        if real_script_hex == "5120" + "00"*32:
             logger.error("❌ [SECURITY] Safety Check Triggered: Blocked attempt to use NULL scriptPubKey!")
             return "Error: Safety Check Blocked Null Address Burn. Please check Enclave state."

        # Construct the PsbtRequest JSON
        psbt_request = {
            "inputs": [{
                "txid": real_utxo['txid'],
                "vout": int(real_utxo.get('vout', 0)),
                "amount_sats": int(real_utxo.get('value', 0)),
                "script_pubkey_hex": real_script_hex
            }],
            "outputs": [{
                "address": to_address,
                "amount_sats": int(amount_sats)
            }],
            "op_return_message": "¯\\_(ツ)_/¯ I M A WALLET AGENT: Sovereign by Math, Free by Code.",
            "fee_rate_sat_vb": float(fee_rate)
        }
        
        logger.info(f"⚙️ [BUILD] Dispatching PSBT construction payload for {amount_sats} sats...")
        json_payload = json.dumps(psbt_request)
        b64_payload = base64.b64encode(json_payload.encode()).decode()
        
        binary_path = os.getenv("ENCLAVE_PSBT_GEN_PATH", os.path.expanduser("~/Documents/GitHub/agent-bitcoin-wallet/enclave-signer/target/release/gen_live_psbt"))
        res = subprocess.run(
            [binary_path, b64_payload],
            capture_output=True, text=True, check=True
        )
        psbt_b64 = res.stdout.strip()
        logger.info("⚙️ [GEN] Dynamically generated PSBT with REAL INPUT.")
        
        resp = bridge.sign_transaction(psbt_b64, provided_signatures, amount_sats=amount_sats)
        if resp.get("type") == "Signature":
             # 🚀 AUTONOMOUS BROADCAST
             signed_psbt = resp.get('signed_psbt_base64')
             txid = resp.get('txid', 'unknown_txid')
             raw_hex = resp.get('raw_hex')

             if raw_hex:
                 try:
                     logger.info(f"🚀 [BROADCAST] Submitting {txid} to Mutinynet...")
                     # 🚀 LIVE BROADCAST: POST to Mutinynet Signet
                     api_url = os.getenv("MUTINYNET_API_URL", "https://mutinynet.com/api")
                     broadcast_url = f"{api_url}/tx"
                     response = requests.post(broadcast_url, data=raw_hex)
                     if response.status_code == 200:
                        explorer_base = os.getenv("MUTINYNET_EXPLORER_URL", "https://mutinynet.com")
                        explorer_url = f"{explorer_base}/tx/{txid}"
                        # 🌐 AUTOMATED VISUALIZATION: Open Chrome on macOS
                        try:
                            # Direct 'open' command is most reliable for background processes on macOS
                            subprocess.run(["open", explorer_url], check=False)
                            webbrowser.open(explorer_url) 
                        except:
                            pass
                            
                        return (f"🚀 TRANSACTION BROADCAST SUCCESS!\n"
                                f"TXID: {txid}\n\n"
                                f"🔗 CLICKABLE LINK: {explorer_url}\n\n"
                                f"*(The legendary message has been etched into the OP_RETURN)*")
                     else:
                         return f"Signed, but Mutinynet API Error ({response.status_code}): {response.text}. TXID: {txid}"
                 except Exception as e:
                     logger.error(f"Broadcast failed: {e}")
                     return f"Signed locally, but broadcast failed: {e}. PSBT: {signed_psbt[:20]}..."
             
             return f"Transaction authorized and signed. PSBT: {signed_psbt[:20]}..."
        return f"Policy Violation: {resp.get('error', 'Rejection')}"
    except Exception as e:
        return f"Bridge Error: {e}"

@mcp.tool()
def get_governance_policy() -> str:
    """Consults the current active governance policy sealed in the Enclave."""
    try:
        resp = bridge.get_policy()
        if resp.get("type") == "Policy":
            p = resp['policy']
            whale_type = p['whale_policy']['type']
            recovery_type = p['recovery_policy']['type']
            return (f"Active Policy: {p['description']} (Version {p['version']})\n"
                    f"Autonomous Allowance: {p['allowance_sats']} sats\n"
                    f"Whale Path: {whale_type}\n"
                    f"Recovery Path: {recovery_type}\n"
                    f"Root Hash: {bytes(p['current_script_hash']).hex()}")
        return f"Error: {resp.get('error', 'Policy retrieval failed')}"
    except Exception as e:
        return f"Bridge Error: {e}"

@mcp.tool()
def propose_policy_update(new_policy_description: str = None, new_allowance_sats: int = None) -> str:
    """Proposes a governance update. Renders a QR code to /dev/tty."""
    gov_psbt_mock = "cHNidP8BAF4CAAAAAa2Xi3LuygT1I9xQr8vqKsJjhaLEFtLwIffZP9jCqehVAQAAAAD/////ATAbDwAAAAAAIlEgpDGQqNn09SbymYZ0pUWpxeGs9iksg0Un59+u43Y4StAAAAAAAAAA"
    try:
        qr = qrcode.QRCode(version=None, box_size=1, border=2)
        qr.add_data(f"bitcoin:?psbt={gov_psbt_mock}")
        qr.make(fit=True)
        matrix = qr.get_matrix()
        
        W, B, R = "\033[107m", "\033[30m", "\033[0m"
        qr_lines = []
        for r in range(0, len(matrix), 2):
            line = ""
            for c in range(len(matrix[r])):
                top = matrix[r][c]
                bottom = matrix[r+1][c] if r + 1 < len(matrix) else False
                if top and bottom: char = "█"
                elif top and not bottom: char = "▀"
                elif not top and bottom: char = "▄"
                else: char = " "
                line += char
            qr_lines.append(f"    {W}{B}{line}{R}")

        with open('/dev/tty', 'w') as tty:
            tty.write("\n" * 5 + "    🚨 GOVERNANCE AUTHORIZATION REQUIRED\n" + "="*40 + "\n\n")
            for line in qr_lines: tty.write(line + "\n")
            tty.write("\n" + "="*40 + "\n" + "\n" * 5)
            tty.flush()
    except Exception as e:
        logger.warning(f"Failed to render QR to tty: {e}")

    resp = bridge.update_policy(new_allowance=new_allowance_sats, psbt_base64=gov_psbt_mock)
    status = "SUCCESS" if resp.get("type") == "Success" else "REJECTED"
    return f"Governance Proposal {status}. Check your terminal for the authorization QR code."

if __name__ == "__main__":
    try:
        logger.info("🚀 [RUN] Starting MCP tool loop...")
        mcp.run()
    except Exception as e:
        logger.critical(f"💥 [CRASH] MCP Loop failed: {e}")

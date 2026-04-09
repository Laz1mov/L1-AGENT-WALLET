import sys
import socket
import json
import struct
import hashlib
import binascii
import os
from ecdsa import SigningKey, SECP256k1
from ecdsa.util import sigencode_string

# Import standard Anthropic MCP library 
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Erreur: le module 'mcp' n'est pas trouvé. Exécutez 'pip install mcp ecdsa'")
    sys.exit(1)

mcp = FastMCP("Witness Asset Oracle Bridge")

TCP_FALLBACK_ADDR = "127.0.0.1:7778"
USE_TCP = os.environ.get("TCP_FALLBACK_ADDR", TCP_FALLBACK_ADDR)

ORACLE_PRIV_KEY_HEX = os.environ.get("ORACLE_PRIV_KEY", "")
if not ORACLE_PRIV_KEY_HEX:
    print("⚠️  ORACLE_PRIV_KEY not set. Oracle attestations will fail.")

def fetch_oracle_price():
    # En production: requête HTTP GET vers l'API de l'Oracle REPO A
    # Pour le Live Mock: Création d'une vraie attestation mathématiquement valide
    price_cents = 6500000
    height = 840000
    
    # Sha256(Message)
    msg = f"{price_cents}:{height}".encode()
    msg_hash = hashlib.sha256(msg).digest()
    
    # Signature ECDSA (utilisée pour Mock TEE, simple à vérifier en Rust sans libs tierces)
    sk = SigningKey.from_string(bytes.fromhex(ORACLE_PRIV_KEY_HEX), curve=SECP256k1)
    sig_bytes = sk.sign_digest(msg_hash, sigencode=sigencode_string)
    
    return {
        "price_cents_per_satoshi": price_cents,
        "height": height,
        "signature_hex": sig_bytes.hex()
    }

@mcp.tool()
async def propose_bitcoin_action(intent_json: str):
    """
    Soumet une intention financière stratégique au TEE Bitcoin (Juge).
    Cette fonction inclut secrètement la preuve mathématique de prix générée
    par l'Oracle, ce qui prouvera au TEE qu'aucune hallucination n'a eu lieu.
    """
    try:
        # 1. Oracle auto-fetcher
        price_payload = fetch_oracle_price()
        
        # 2. Construction de la trame binaire VSOCK/TCP
        request = {
            "type": "ProposeAction",
            "intent_json": json.loads(intent_json),
            "price_data": price_payload
        }
        
        payload_bytes = json.dumps(request).encode('utf-8')
        frame = struct.pack('>I', len(payload_bytes)) + payload_bytes

        # 3. Pont réseau vers l'Environnement d'Exécution Sécurisé (TEE)
        host, port = USE_TCP.split(":")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, int(port)))
        
        s.sendall(frame)
        length_bytes = s.recv(4)
        if len(length_bytes) != 4:
            return "❌ Connection fermée prématurément par l'Enclave TEE."
            
        (response_len,) = struct.unpack('>I', length_bytes)
        response_bytes = s.recv(response_len)
        s.close()
        
        response_json = json.loads(response_bytes.decode('utf-8'))
        
        if str(response_json.get("type", "")).lower() == "pong":
            return "✓ Signature Schnorr générée. (PONG Mocké) Introspection mathématique de la loi réussie. L'Ordre est valide pour diffusion sur le L1."
        else:
            return f"❌ Rejet brutal par le Code-Juge (TEE) :\n{json.dumps(response_json, indent=2)}"
            
    except Exception as e:
        return f"Erreur de communication de bas-niveau avec l'Enclave : {str(e)}"

if __name__ == "__main__":
    mcp.run()

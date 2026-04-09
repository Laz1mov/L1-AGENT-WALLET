#!/usr/bin/env python3
import sys
import json
import hashlib
import binascii
from secp256k1 import PrivateKey

def sign_mandate(manifest_str: str, private_key_hex: str):
    try:
        # 1. Parse Manifest (Ensuring deterministic serialization)
        manifest = json.loads(manifest_str)
        serialized = json.dumps(manifest, separators=(',', ':'))
        
        # 2. Hash Manifest (SHA-256)
        msg_hash = hashlib.sha256(serialized.encode()).digest()
        
        # 3. Sign (Schnorr BIP-340)
        sk = PrivateKey(binascii.unhexlify(private_key_hex))
        # Note: raw=True treats the input as the finalized 32-byte message to be signed.
        signature = sk.schnorr_sign(msg_hash, b'', raw=True)
        sig_hex = binascii.hexlify(signature).decode()
        
        print("\n" + "━"*40)
        print("🏛️  SOVEREIGN MANDATE SIGNATURE")
        print("━"*40)
        print(f"Manifest Hash : {binascii.hexlify(msg_hash).decode()}")
        print(f"Master Pubkey : {binascii.hexlify(sk.pubkey.serialize()[1:]).decode()}")
        print(f"Signature Hex : \033[1;32m{sig_hex}\033[0m")
        print("━"*40)
        print("\nCopy the Signature Hex and provide it to the Agent tool.")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 sign_mandate.py '<manifest_json>' <private_key_hex>")
        sys.exit(1)
    
    sign_mandate(sys.argv[1], sys.argv[2])

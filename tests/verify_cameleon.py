import os
import sys
import json
import socket
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import sovereign_batch_mint

def query_enclave(request):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(('localhost', 7777))
        s.sendall(json.dumps(request).encode())
        data = s.recv(16384)
        return json.loads(data.decode())

print("🔍 QUERYING CAMÉLÉON IDENTITY...")
resp = query_enclave({"type": "GetPolicy"})
address = resp.get("address")
print(f"📍 CURRENT ADDRESS (Index 0): {address}")

print("\n⚙️  RUNNING ON-CHAIN AUDIT (MANDATE 2)...")
is_burned = sovereign_batch_mint.audit_on_chain_exposure(address)
if not is_burned:
    print("✅ IDENTITY STATUS: PRISTINE (No Script Path Spend Detected)")
else:
    print("🚨 IDENTITY STATUS: BURNED (Reveal Detected!)")

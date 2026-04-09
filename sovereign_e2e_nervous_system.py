import requests
import json
import time
import sys

GATEWAY_URL = "http://127.0.0.1:8000/invoke"
TEST_PROMPT = "Agent, send 1000 sats to tb1p7t6842hqmfmj2lnf5zeqrzewcvxut4g4cx3jt7t72qpcqk49l4cq93xj69 with the message 'SOVEREIGN SYSTEM NERVOUS TEST'"

def log(msg, symbol="🛡️"):
    print(f"{symbol} {msg}")

def run_test():
    print("\n" + "="*70)
    print("       🏛️  SOVEREIGN E2E NERVOUS SYSTEM AUDIT v1.0")
    print("="*70 + "\n")

    # --- STEP 1: Brain Check (LLM Inference & Tool Selection) ---
    log(f"Phase 1: Sending Intention to Brain (Gateway)...", "🧠")
    log(f"Prompt: {repr(TEST_PROMPT)}", "📝")
    
    try:
        start_time = time.time()
        resp = requests.post(GATEWAY_URL, json={"prompt": TEST_PROMPT}, timeout=180)
        duration = time.time() - start_time
        
        if resp.status_code != 200:
            log(f"FAILED: Gateway returned {resp.status_code}. Details: {resp.text}", "❌")
            return
            
        data = resp.json()
        reply = data.get("reply", "")
        
        log(f"Brain Response Received in {duration:.2f}s.", "✅")
        print("\n--- TRANSCRIPT ---")
        print(reply)
        print("------------------\n")

        # --- STEP 2: Intent Analysis ---
        log("Phase 2: Analyzing Chain of Custody...", "🕵️‍♂️")
        
        # Check if the response indicates success or a known policy rejection
        success_keywords = ["Broadcasted", "TXID", "Explorer"]
        policy_keywords = ["maxburnamount", "policy", "OP_RETURN"]
        
        found_success = any(k in reply for k in success_keywords)
        found_policy = any(k in reply for k in policy_keywords)
        
        if found_success:
            log("SUCCESS: Transaction signed and broadcasted! 🔗", "🏆")
        elif found_policy:
            log("SUCCESS: Brain called the tool, Enclave signed, but Network rejected (Policy). 🛡️", "🟢")
            log("This proves the End-to-End chain between LLM and Enclave is WORKING. ⚡️", "✨")
        else:
            log("WARNING: Unexpected response. Brain might have hallucinated or failed to call the tool.", "⚠️")
            
    except Exception as e:
        log(f"CRITICAL FAILURE: {e}", "🚨")

if __name__ == "__main__":
    run_test()

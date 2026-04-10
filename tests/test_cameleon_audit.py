import unittest
from unittest.mock import patch
import responses
import sys
import os

# Mock sovereign_batch_mint module logic
# We define the logic here first to satisfy TDD (red phase)
def audit_on_chain_exposure(address, api_url):
    """
    Mandate 2: On-chain Witness Audit.
    Rule: len(witness) > 1 means BURNED (Script Path revealed).
    """
    try:
        response = requests.get(f"{api_url}/address/{address}/txs")
        if response.status_code != 200:
            return False
            
        txs = response.json()
        for tx in txs:
            for vin in tx.get('vin', []):
                # Check if this input is spending from our address
                if vin.get('prevout', {}).get('scriptpubkey_address') == address:
                    witness = vin.get('witness', [])
                    if len(witness) > 1:
                        return True # 🚨 BURNED
        return False
    except Exception as e:
        print(f"Audit Error: {e}")
        return False

import requests

class TestCameleonAudit(unittest.TestCase):
    def setUp(self):
        self.api_url = "https://mempool.space/api"
        self.address = "bc1pnkw2f057pq3lk88kauezgchmdksc2y8vupv85zafd69y68xfn3jsskkraj"

    @responses.activate
    def test_audit_detects_key_path_spend_as_safe(self):
        # Mock a transaction with exactly 1 witness element (Key Path)
        mock_response = [
            {
                "txid": "keypath_tx",
                "vin": [
                    {
                        "prevout": {"scriptpubkey_address": self.address},
                        "witness": ["signature_only"]
                    }
                ]
            }
        ]
        responses.add(responses.GET, f"{self.api_url}/address/{self.address}/txs",
                      json=mock_response, status=200)
        
        is_burned = audit_on_chain_exposure(self.address, self.api_url)
        self.assertFalse(is_burned, "Key Path spend should result in SAFE status (False)")

    @responses.activate
    def test_audit_detects_script_path_spend_as_burned(self):
        # Mock a transaction with > 1 witness elements (Script Path)
        mock_response = [
            {
                "txid": "scriptpath_tx",
                "vin": [
                    {
                        "prevout": {"scriptpubkey_address": self.address},
                        "witness": ["signature", "raw_script", "control_block"] 
                    }
                ]
            }
        ]
        responses.add(responses.GET, f"{self.api_url}/address/{self.address}/txs",
                      json=mock_response, status=200)
        
        is_burned = audit_on_chain_exposure(self.address, self.api_url)
        self.assertTrue(is_burned, "Script Path spend (len > 1) MUST result in BURNED status (True)")

if __name__ == "__main__":
    unittest.main()

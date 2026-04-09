"""
mcp-bridge/bridge.py

EnclaveBridge — Pont bas niveau vers le TEE Souverain (Rust / PRECOP Enclave).

Wire protocol (mirrors enclave/src/protocol.rs):
  [4-byte big-endian length][UTF-8 JSON payload]

Ce bridge est utilisé par server.py pour toutes les opérations wallet.
Il implémente le protocole God Protocol complet :
  1. ProposeAction (intent + Oracle price + Utreexo proofs)
  2. GetBalance
  3. Ping (health check)

Il NE contient pas de logique financière.  Toute décision de covenant
est prise par le TEE.  Ce module est un simple canal de communication.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sovereign.bridge")

# ─── Constants ─────────────────────────────────────────────────────────────────

AF_VSOCK: int = 40           # Linux VSOCK socket family
MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # 4 MiB hard cap


# ─── Frame helpers (mirrors vsock_client.py) ───────────────────────────────────

def _send_frame(sock: socket.socket, payload: Dict[str, Any]) -> None:
    """Send raw JSON payload."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock.sendall(data)


def _recv_frame(sock: socket.socket) -> Dict[str, Any]:
    """Receive and decode raw JSON from the socket (Robust Audit Logic)."""
    # Wait for the first chunk with a short timeout
    sock.settimeout(5.0)
    data = sock.recv(MAX_RESPONSE_BYTES)
    if not data:
        raise ConnectionError("Socket closed without response")
    
    logger.info(f"📥 Bridge received raw data (LEN={len(data)}): {data[:100]}...")
    
    # Simple decode and parse
    try:
        return json.loads(data.decode("utf-8").strip())
    except json.JSONDecodeError as e:
        logger.error(f"Malformed JSON from enclave: {data}")
        raise ValueError(f"Enclave response was not valid JSON: {e}")


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed before all bytes received")
        buf.extend(chunk)
    return bytes(buf)


def _make_socket(
    host: str, port: int, vsock_cid: Optional[int] = None
) -> socket.socket:
    """
    Open a connected socket.
    Uses AF_VSOCK when vsock_cid is set and AF_VSOCK is available.
    Falls back to TCP otherwise.
    """
    if vsock_cid is not None:
        try:
            sock = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
            sock.connect((vsock_cid, port))
            logger.debug("VSOCK connected → CID=%d PORT=%d", vsock_cid, port)
            return sock
        except OSError as exc:
            logger.warning("VSOCK unavailable (%s), falling back to TCP", exc)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    logger.debug("TCP connected → %s:%d", host, port)
    return sock


# ─── EnclaveBridge ─────────────────────────────────────────────────────────────

class EnclaveBridge:
    """
    Low-level communication bridge to the PRECOP Sovereign Enclave (Rust).

    Handles the 4-byte length-prefixed JSON framing defined in
    enclave/src/protocol.rs.

    Environment variables:
      ENCLAVE_HOST      — TCP host (default: 127.0.0.1)
      ENCLAVE_PORT      — TCP port (default: 5005)
      ENCLAVE_VSOCK_CID — VSOCK CID (if set, uses AF_VSOCK instead of TCP)
      ENCLAVE_TIMEOUT   — socket timeout in seconds (default: 30)
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        vsock_cid: Optional[int] = None,
        timeout: float = 30.0,
    ):
        self.host = host or os.getenv("ENCLAVE_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("ENCLAVE_PORT", "5005"))
        self.timeout = float(os.getenv("ENCLAVE_TIMEOUT", str(timeout)))

        cid_env = os.getenv("ENCLAVE_VSOCK_CID")
        self.vsock_cid = vsock_cid or (int(cid_env) if cid_env else None)

    # ── Core dispatcher ────────────────────────────────────────────────────────

    def _send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Open a connection, send one request, receive one response, close."""
        try:
            logger.info(f"🔌 Bridge connecting to enclave at {self.host}:{self.port}...")
            sock = _make_socket(self.host, self.port, self.vsock_cid)
            sock.settimeout(self.timeout)
            try:
                _send_frame(sock, request)
                return _recv_frame(sock)
            finally:
                sock.close()
        except ConnectionRefusedError:
            logger.error(f"❌ Connection refused at {self.host}:{self.port}")
            return {
                "type": "error",
                "code": "ENCLAVE_UNREACHABLE",
                "message": f"Enclave offline at {self.host}:{self.port}",
            }
        except Exception as e:
            logger.error(f"Bridge connection error: {e}")
            return {"type": "error", "message": f"Enclave offline or unauthorized: {str(e)}"}

    # ── God Protocol operations ────────────────────────────────────────────────

    def ping(self) -> bool:
        """Health check — returns True if the enclave replies with PolicyReport."""
        resp = self._send_request({"type": "GetPolicy"})
        return resp.get("type") == "Policy"

    def get_balance(self) -> Dict[str, Any]:
        """Query the enclave for policy state (which includes balance info)."""
        return self._send_request({"type": "GetPolicy"})

    def propose_action(
        self,
        intent_dict: Dict[str, Any],
        price_data: Dict[str, Any],
        frost_mock_data: Optional[Dict[str, Any]] = None,
        parent_txs: Optional[Dict[str, str]] = None,
        utreexo_proofs: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Submit a ProposeAction to the enclave.

        The enclave runs the full God Protocol pipeline:
          Oracle attestation → Intent parse → Covenant check → Utreexo verify → FROST sign

        Parameters
        ----------
        intent_dict:
            Agent intent as a Python dict.
        price_data:
            SignedPricePayload dict from the Oracle.
            Required fields: price_cents_per_satoshi, height, fee_rate_sat_vb, signature_hex.
            Optional: utreexo_roots_hex, utreexo_num_leaves.
        frost_mock_data:
            Optional co-signer data for one-shot FROST signing (CI / TDD only).
        parent_txs:
            Optional {txid_hex: raw_tx_hex} map for UTXO amount introspection.
        utreexo_proofs:
            Optional list of UtreexoProof dicts (Trustless Enclave Signer).
            In SGX mode with an initialised accumulator, these are mandatory.

        Returns
        -------
        dict
            Raw response dict from the enclave.  Check `type` field:
              "signed_tx"          → intent approved, tx broadcast
              "balance_report"     → get_balance result
              "covenant_rejection" → CDP invariant violated (LLM must self-correct)
              "error"              → hard error
        """
        request: Dict[str, Any] = {
            "type": "propose_action",
            "intent_json": intent_dict,
            "price_data": price_data,
            "frost_mock_data": frost_mock_data,
            "parent_txs": parent_txs,
        }
        if utreexo_proofs is not None:
            request["utreexo_proofs"] = utreexo_proofs

        return self._send_request(request)

    # ── Legacy operations (backward compat) ────────────────────────────────────

    def get_address(self) -> Dict[str, Any]:
        """[Legacy] Retrieve the sovereign Taproot address from the enclave."""
        return self._send_request({"type": "get_balance"})

    def sign_transaction(
        self,
        psbt_base64: str,
        provided_signatures: Optional[List[str]] = None,
        amount_sats: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        [Legacy] Direct PSBT signing request.

        In the God Protocol architecture, use `propose_action()` instead.
        This method is kept for backward compatibility with server.py.
        """
        logger.warning(
            "sign_transaction() is a legacy API. "
            "Prefer propose_action() for full God Protocol compliance."
        )
        request = {
            "type": "SignTransaction",
            "psbt_base64": psbt_base64,
            "amount_sats": amount_sats,
            "provided_signatures": provided_signatures or [],
        }
        return self._send_request(request)

    def get_policy(self) -> Dict[str, Any]:
        """Retrieve the current governance policy and address."""
        return self._send_request({"type": "GetPolicy"})

    def update_policy(
        self,
        new_whale_policy: Optional[Any] = None,
        new_recovery_policy: Optional[Any] = None,
        new_allowance: Optional[int] = None,
        upgrade_proof: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a policy update request (Aligned with Enclave schema)."""
        request = {
            "type": "UpdatePolicy",
            "new_whale_policy": new_whale_policy,
            "new_recovery_policy": new_recovery_policy,
            "new_allowance": new_allowance,
            "upgrade_proof": upgrade_proof,
        }
        return self._send_request(request)
    def sign_batch_chain(
        self,
        psbt_base64: str,
        batch_manifest: Dict[str, Any],
        mandate_signature: str,
    ) -> Dict[str, Any]:
        """
        Request signing of a Daisy-Chain batch (up to 25 transactions).
        Bypasses allowance if mandate_signature is valid for the manifest.
        """
        request = {
            "type": "SignBatchChain",
            "psbt_base64": psbt_base64,
            "batch_manifest": batch_manifest,
            "mandate_signature": mandate_signature,
        }
        return self._send_request(request)

    def sign_legacy_sweep(self, psbt_base64: str) -> Dict[str, Any]:
        """
        Request signing of a Legacy (BIP-86) sweep transaction.
        Signs for inputs at the BIP-86 address derived from internal key.
        """
        request = {
            "type": "SignLegacySweep",
            "psbt_base64": psbt_base64,
        }
        return self._send_request(request)

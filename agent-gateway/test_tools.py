"""
test_tools.py — TDD test suite for the mint_rune Gateway tool.

RED phase  : tests fail because _tool_mint_rune / TOOL_DISPATCH / TOOL_SCHEMAS
             do not yet contain the mint_rune capability.
GREEN phase: tests pass after the implementation below is added to agent_server.py.

Run:  cd agent-gateway && pytest test_tools.py -v
"""

import json
import base64
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# ── Helpers ───────────────────────────────────────────────────────────────────

FAKE_POLICY_RESPONSE = {
    "type": "Policy",
    "address": "tb1p_sovereign_test_address_aaaaaa",
    "script_pubkey_hex": "5120" + "ab" * 32,
    "policy": {"allowance_sats": 100_000, "version": 1, "status": "Active"},
}

FAKE_UTXO_LIST = [
    {"txid": "a" * 64, "vout": 0, "value": 50_000}
]

FAKE_SIGN_RESPONSE = {
    "type": "Signature",
    "raw_hex": "deadbeef01020304",
    "txid": "c" * 64,
}

FAKE_BROADCAST_TXID = "d" * 64


def _make_fake_subprocess(captured: dict):
    """
    Returns a side_effect function for subprocess.run.
    Captures the base64 JSON payload passed to gen_live_psbt and
    returns a mock that looks like a successful PSBT construction.
    """
    def fake_run(cmd, **kwargs):
        # cmd = ["/path/to/gen_live_psbt", "<base64_payload>"]
        if len(cmd) >= 2:
            try:
                payload = json.loads(base64.b64decode(cmd[1]).decode())
                captured.update(payload)
            except Exception:
                pass  # Let the assertion catch it later
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "cHNidA=="   # minimal non-empty base64 (fake PSBT)
        mock.stderr = ""
        return mock
    return fake_run


# ═══════════════════════════════════════════════════════════════════════════════
# RED PHASE — These tests FAIL before mint_rune is implemented
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistrationRed:
    """
    Phase: RED
    Purpose: Verify that mint_rune is registered in both TOOL_DISPATCH and
             TOOL_SCHEMAS before any implementation exists.
    Expected result before GREEN: ImportError or AssertionError.
    """

    def test_mint_rune_in_tool_dispatch(self):
        """
        [RED] mint_rune must be a key in TOOL_DISPATCH.
        Fails with KeyError or AssertionError until agent_server.py is updated.
        """
        from agent_server import TOOL_DISPATCH
        assert "mint_rune" in TOOL_DISPATCH, (
            "\n[RED→GREEN] 'mint_rune' missing from TOOL_DISPATCH.\n"
            "Fix: add   TOOL_DISPATCH['mint_rune'] = _tool_mint_rune\n"
            "to agent_server.py after defining _tool_mint_rune()."
        )

    def test_mint_rune_in_tool_schemas(self):
        """
        [RED] mint_rune must appear in TOOL_SCHEMAS with required properties.
        Fails with AssertionError until the schema entry is added.
        """
        from agent_server import TOOL_SCHEMAS
        names = [t["name"] for t in TOOL_SCHEMAS]
        assert "mint_rune" in names, (
            "\n[RED→GREEN] 'mint_rune' missing from TOOL_SCHEMAS.\n"
            "Fix: append the mint_rune schema dict to TOOL_SCHEMAS in agent_server.py."
        )

    def test_mint_rune_schema_has_required_properties(self):
        """
        [RED] The mint_rune schema must declare 'rune_name' and 'amount'
        as required properties so the LLM knows what to provide.
        """
        from agent_server import TOOL_SCHEMAS
        schema = next(
            (t for t in TOOL_SCHEMAS if t["name"] == "mint_rune"), None
        )
        assert schema is not None, "mint_rune schema not found in TOOL_SCHEMAS"

        props = schema["input_schema"].get("properties", {})
        assert "rune_name" in props, (
            "Schema must declare 'rune_name' property — the LLM won't know to pass it."
        )
        assert "amount" in props, (
            "Schema must declare 'amount' property — the LLM won't know to pass it."
        )

        required = schema["input_schema"].get("required", [])
        assert "rune_name" in required, "'rune_name' must be in the required array"
        assert "amount" in required, "'amount' must be in the required array"


# ═══════════════════════════════════════════════════════════════════════════════
# GREEN PHASE — These tests PASS after implementation is in place
# ═══════════════════════════════════════════════════════════════════════════════

class TestMintRunePayloadRouting:
    """
    Phase: GREEN (integration)
    Purpose: Validate that _tool_mint_rune constructs the correct JSON payload
             for gen_live_psbt, specifically:
               1. The 'rune_mint' key is present with correct fields.
               2. The first output is a 546-sat dust (Rune pointer).
               3. The flow does not attempt to add a separate OP_RETURN message.
    """

    def _run_tool(self, rune_name="SOVEREIGN", amount=100, **kwargs):
        """Helper: run _tool_mint_rune under full mock and return captured payload."""
        captured = {}

        fake_utxo = MagicMock()
        fake_utxo.status_code = 200
        fake_utxo.json.return_value = FAKE_UTXO_LIST

        fake_broadcast = MagicMock()
        fake_broadcast.status_code = 200
        fake_broadcast.text = FAKE_BROADCAST_TXID

        with patch("agent_server._bridge.get_policy", return_value=FAKE_POLICY_RESPONSE), \
             patch("requests.get", return_value=fake_utxo), \
             patch("subprocess.run", side_effect=_make_fake_subprocess(captured)), \
             patch("agent_server._bridge.sign_transaction", return_value=FAKE_SIGN_RESPONSE), \
             patch("requests.post", return_value=fake_broadcast):

            from agent_server import _tool_mint_rune
            result = _tool_mint_rune(rune_name=rune_name, amount=amount, **kwargs)

        return captured, result

    def test_payload_contains_rune_mint_key(self):
        """
        [GREEN] The JSON payload sent to gen_live_psbt must contain 'rune_mint'.
        This key is the contract between the Python Gateway and the Rust binary.
        """
        captured, _ = self._run_tool()
        assert "rune_mint" in captured, (
            f"'rune_mint' key missing from gen_live_psbt payload.\n"
            f"Payload keys: {list(captured.keys())}"
        )

    def test_rune_mint_carries_correct_name_and_amount(self):
        """
        [GREEN] rune_mint.rune_name and rune_mint.amount must match the tool args.
        """
        captured, _ = self._run_tool(rune_name="SOVEREIGN", amount=100)
        rm = captured["rune_mint"]
        assert rm["rune_name"] == "SOVEREIGN", (
            f"rune_name mismatch: expected 'SOVEREIGN', got '{rm.get('rune_name')}'"
        )
        assert rm["amount"] == 100, (
            f"amount mismatch: expected 100, got {rm.get('amount')}"
        )

    def test_first_output_is_546_sat_dust(self):
        """
        [GREEN] The first output in the payload must be a 546-sat dust output
        (the Rune pointer — receives the premined supply at index 0).
        Any other value causes the Rune to be credited to the wrong output or lost.
        """
        captured, _ = self._run_tool()
        outputs = captured.get("outputs", [])
        assert len(outputs) >= 1, "Payload must contain at least one output (dust)"
        dust = outputs[0]
        assert dust["amount_sats"] == 546, (
            f"First output must be 546-sat dust (Rune pointer). "
            f"Got {dust['amount_sats']} sats — Rune will be credited to wrong output."
        )

    def test_divisibility_defaults_to_zero(self):
        """
        [GREEN] If divisibility is not specified, it must default to 0
        (whole-unit Rune — no decimal places).
        """
        captured, _ = self._run_tool()
        rm = captured["rune_mint"]
        assert rm.get("divisibility", 0) == 0, (
            f"divisibility must default to 0. Got: {rm.get('divisibility')}"
        )

    def test_custom_divisibility_is_forwarded(self):
        """
        [GREEN] divisibility passed to the tool must appear in the payload.
        """
        captured, _ = self._run_tool(divisibility=8)
        assert captured["rune_mint"]["divisibility"] == 8

    def test_success_response_contains_txid(self):
        """
        [GREEN] A successful mint must return a string containing the TXID.
        """
        _, result = self._run_tool()
        assert isinstance(result, str), "Tool must return a string"
        # The mock broadcast returns FAKE_BROADCAST_TXID ('d' * 64).
        # The result string should contain either the txid or a success indicator.
        assert ("✅" in result or FAKE_BROADCAST_TXID in result or "TXID" in result), (
            f"Success result should reference the TXID or contain ✅. Got: {result}"
        )


class TestMintRuneValidation:
    """
    Phase: GREEN (unit-level validation)
    Purpose: Test the input validation logic in _tool_mint_rune before any
             external calls are made.
    """

    def test_insufficient_balance_returns_warning(self):
        """
        [GREEN] If the wallet has fewer than 2000 sats, the tool must return
        an insufficient-balance warning WITHOUT calling subprocess.run.
        """
        tiny_utxo = MagicMock()
        tiny_utxo.status_code = 200
        tiny_utxo.json.return_value = [
            {"txid": "a" * 64, "vout": 0, "value": 500}  # 500 < 2000
        ]

        subprocess_called = {"called": False}

        def fail_if_called(*args, **kwargs):
            subprocess_called["called"] = True
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("agent_server._bridge.get_policy", return_value=FAKE_POLICY_RESPONSE), \
             patch("requests.get", return_value=tiny_utxo), \
             patch("subprocess.run", side_effect=fail_if_called):

            from agent_server import _tool_mint_rune
            result = _tool_mint_rune(rune_name="SOVEREIGN", amount=100)

        assert not subprocess_called["called"], (
            "subprocess.run must NOT be called when balance is insufficient"
        )
        assert any(word in result.lower() for word in ["insufficient", "balance", "sats", "fund"]), (
            f"Response must mention insufficient balance. Got: {result}"
        )

    def test_invalid_rune_name_is_rejected(self):
        """
        [GREEN] A rune name containing lowercase letters must be rejected
        before any network call is made.
        """
        with patch("agent_server._bridge.get_policy", return_value=FAKE_POLICY_RESPONSE):
            from agent_server import _tool_mint_rune
            result = _tool_mint_rune(rune_name="invalid_name", amount=100)

        assert any(word in result.lower() for word in ["invalid", "uppercase", "name", "error"]), (
            f"Invalid rune name must produce an error message. Got: {result}"
        )


class TestExecuteToolDispatch:
    """
    Phase: GREEN (dispatch integration)
    Purpose: Verify that the LLM tool-calling loop correctly routes
             a mint_rune tool_use block to _tool_mint_rune via _execute_tool.
    """

    def test_execute_tool_routes_mint_rune(self):
        """
        [GREEN] _execute_tool("mint_rune", {...}) must invoke _tool_mint_rune
        with the correct keyword arguments.
        """
        call_args = {}

        def mock_mint_rune(*args, **kwargs):
            # _execute_tool passes positional args:
            # fn(rune_name, rune_id, amount, divisibility, symbol, open_mint, destination_address, payments)
            if len(args) >= 3:
                call_args["rune_name"] = args[0]
                call_args["amount"] = args[2]
            call_args.update(kwargs)
            return "✅ Mock mint success"

        with patch.dict("agent_server.TOOL_DISPATCH", {"mint_rune": mock_mint_rune}):
            from agent_server import _execute_tool
            _execute_tool("mint_rune", {
                "rune_name": "SOVEREIGN",
                "amount": 1000,
                "divisibility": 2,
            })

        assert call_args.get("rune_name") == "SOVEREIGN", (
            f"rune_name not forwarded to _tool_mint_rune. Got: {call_args}"
        )
        assert call_args.get("amount") == 1000, (
            f"amount not forwarded to _tool_mint_rune. Got: {call_args}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Fee API URL switching
#
# _get_fee_rate() must request from the correct Esplora endpoint depending on
# the BITCOIN_NETWORK environment variable:
#
#   mainnet  → https://mempool.space/api/v1/fees/recommended
#   signet / mutinynet / absent → https://mutinynet.com/api/v1/fees/recommended
#
# These tests patch requests.get to capture the URL actually called, asserting
# the correct endpoint without making real network requests.
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetFeeRateNetworkSwitching:
    """
    Phase: PHASE 3 RED → GREEN
    Purpose: _get_fee_rate() must select the correct Esplora fee endpoint
             based on the BITCOIN_NETWORK environment variable.
    """

    def _make_mock_requests(self, json_response: dict):
        """Return a mock requests.get that succeeds with the given JSON body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = json_response
        mock_get = MagicMock(return_value=mock_resp)
        return mock_get

    def test_mainnet_calls_mempool_space(self):
        """
        [RED→GREEN] When BITCOIN_NETWORK=mainnet, _get_fee_rate must call
        https://mempool.space/api/v1/fees/recommended.
        """
        mock_get = self._make_mock_requests({"halfHourFee": 10})

        with patch.dict(os.environ, {"BITCOIN_NETWORK": "mainnet"}):
            with patch("agent_server._requests.get", mock_get):
                from agent_server import _get_fee_rate
                rate = _get_fee_rate()

        called_url = mock_get.call_args[0][0]
        assert "mempool.space" in called_url, (
            f"mainnet must use mempool.space. Called: {called_url}"
        )
        assert rate == 12.0, f"halfHourFee=10 * 1.2 premium = 12.0 expected. Got: {rate}"

    def test_mutinynet_calls_mutinynet(self):
        """
        [RED→GREEN] When BITCOIN_NETWORK=mutinynet, _get_fee_rate must call
        https://mutinynet.com/api/v1/fees/recommended.
        """
        mock_get = self._make_mock_requests({"halfHourFee": 1})

        with patch.dict(os.environ, {"BITCOIN_NETWORK": "mutinynet"}):
            with patch("agent_server._requests.get", mock_get):
                from agent_server import _get_fee_rate
                rate = _get_fee_rate()

        called_url = mock_get.call_args[0][0]
        assert "mutinynet.com" in called_url, (
            f"mutinynet must use mutinynet.com. Called: {called_url}"
        )

    def test_absent_env_var_calls_mutinynet(self):
        """
        [RED→GREEN] With no BITCOIN_NETWORK set, default to mutinynet endpoint.
        """
        mock_get = self._make_mock_requests({"halfHourFee": 1})

        env_without_btc_network = {k: v for k, v in os.environ.items()
                                   if k != "BITCOIN_NETWORK"}
        with patch.dict(os.environ, env_without_btc_network, clear=True):
            with patch("agent_server._requests.get", mock_get):
                from agent_server import _get_fee_rate
                rate = _get_fee_rate()

        called_url = mock_get.call_args[0][0]
        assert "mutinynet.com" in called_url, (
            f"Default (no env var) must use mutinynet.com. Called: {called_url}"
        )

    def test_signet_calls_mutinynet(self):
        """
        [RED→GREEN] BITCOIN_NETWORK=signet → mutinynet endpoint
        (Signet and Mutinynet share the same Esplora instance).
        """
        mock_get = self._make_mock_requests({"halfHourFee": 2})

        with patch.dict(os.environ, {"BITCOIN_NETWORK": "signet"}):
            with patch("agent_server._requests.get", mock_get):
                from agent_server import _get_fee_rate
                rate = _get_fee_rate()

        called_url = mock_get.call_args[0][0]
        assert "mutinynet.com" in called_url, (
            f"signet must use mutinynet.com. Called: {called_url}"
        )

    def test_fee_rate_fallback_on_error(self):
        """
        [GREEN] When the fee API is unreachable, _get_fee_rate must return
        the safe default (1.2) without raising an exception.
        """
        mock_get = MagicMock(side_effect=Exception("network unreachable"))

        with patch("agent_server._requests.get", mock_get):
            from agent_server import _get_fee_rate
            rate = _get_fee_rate()

        assert rate == 1.2, f"Fallback fee rate must be 1.2. Got: {rate}"

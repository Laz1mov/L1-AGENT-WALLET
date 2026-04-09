---
name: bitcoin-transactions
description: >
  Use this skill when the user wants to send Bitcoin, transfer sats, pay
  an address, batch multiple payments, or embed a message in the blockchain.
  Triggers on: "send X sats to tb1p...", "pay this address", "transfer",
  "batch payment", "write a message on-chain", "OP_RETURN message".
  Also use for any spending that exceeds the daily allowance — this skill
  covers the governance escalation path (propose_policy_update).
required_tools:
  - check_my_balance
  - compose_transaction
  - propose_policy_update
---

# Bitcoin Transactions Skill

## Overview

This skill governs all outgoing Bitcoin transfers from the Sovereign Agent
wallet. Transactions are constructed as PSBTs, signed inside the Rust TEE
Enclave, and broadcast to Mutinynet via the Esplora API.

Two tools are available: `compose_transaction` for execution, and
`propose_policy_update` for governance escalation when the spend exceeds
the daily allowance.

## Tools

### `compose_transaction`

Constructs, signs, and broadcasts a Bitcoin transaction in one call.

**Capabilities:**
- Single payment: one address, one amount
- Batch payment: up to 10 outputs in a single transaction
- OP_RETURN message: up to 80 bytes of arbitrary data etched on-chain
- Fee safety trap: automatically aborts if network fee exceeds 50 sat/vB

**Required parameters:**
```json
{
  "payments": [
    { "address": "tb1p...", "amount_sats": 10000 },
    { "address": "tb1p...", "amount_sats": 5000 }
  ]
}
```

**Optional parameters:**
```json
{
  "op_return_message": "Hello Mutinynet",
  "confirmed_high_fee": true
}
```

**When to call:** once you have the destination address(es) and amount(s).
If the user provides multiple recipients, batch them into a single call
(more efficient, saves fees).

**Response format:** on success:
```
✅ Transaction Broadcasted!
TXID: <64-char hex>
Explorer: https://mutinynet.com/tx/<txid>
```
On fee trap:
```
🚨 [FEE TRAP] Network congestion detected (X sat/vB)...
```
The user must explicitly confirm with `confirmed_high_fee: true`.

---

### `propose_policy_update`

Generates a governance QR code (PSBT) for the user to authorize a spending
limit increase via hardware wallet.

**When to call:** ONLY when a spend exceeds the current daily allowance
(visible via `get_governance_policy`). Do not call for normal spends.

**Required parameters:**
```json
{ "new_allowance_sats": 500000 }
```

**Response:** generates a QR code image sent via Telegram for the user to
scan with their hardware signer. The policy update takes effect once the
signed PSBT is submitted back to the enclave.

## Agent Reasoning Protocol

### Step 1 — Extract addresses and amounts

Scan the user message for:
- Taproot addresses starting with `tb1p` (Mutinynet Signet)
- Amounts in sats, mBTC, or BTC (convert to sats)

If multiple address-amount pairs are mentioned, group them into one batched
`compose_transaction` call.

### Step 2 — Check balance

Call `check_my_balance` to confirm the wallet has enough funds:
- Required: sum of all payment amounts + estimated fee (~500–2,000 sat)

If insufficient: report the shortfall and stop. Do not call `compose_transaction`.

### Step 3 — Check allowance

Call `get_governance_policy` if the spend is large or if a prior transaction
was rejected with a policy error.

- If spend ≤ allowance → proceed with `compose_transaction`
- If spend > allowance → call `propose_policy_update` instead

### Step 4 — Execute or escalate

**Normal path:** call `compose_transaction` with the extracted parameters.

**High-fee path:** if the tool returns a fee trap warning, ask the user to
confirm: *"Network fees are high (X sat/vB). This will cost ~Y sats in fees.
Confirm to proceed?"* Then retry with `confirmed_high_fee: true`.

**Allowance path:** call `propose_policy_update` and instruct the user to
scan the QR code with their hardware wallet.

## Security Notes

- **OP_RETURN messages are permanent and public.** Warn the user before
  sending sensitive content on-chain. The 80-byte cap is enforced by the enclave.
- **Mutinynet only.** The address validator enforces Signet network. Mainnet
  addresses will be rejected by `gen_live_psbt`.
- **No double-spend protection at the agent level.** Back-to-back transactions
  using the same UTXO will fail at broadcast. Wait for confirmation between sends.
- **OP_RETURN and Rune mint are mutually exclusive.** A single transaction
  cannot carry both a legacy OP_RETURN message and a Runestone. Use the
  `mint_rune` skill for Runes; use `op_return_message` only for plain text.

## Example Conversations

**User:** "Send 5,000 sats to tb1pqqqqsovereign..."

**Agent reasoning:**
1. Extract: address=`tb1pqqqqsovereign...`, amount=5000 sats
2. Call `check_my_balance` → 50,000 sats ✅
3. Call `compose_transaction` with `payments: [{address: ..., amount_sats: 5000}]`
4. Display TXID receipt

**User:** "Pay Alice 10k and Bob 20k"

**Agent reasoning:**
1. Ask for addresses if not provided, OR extract from context
2. Call `check_my_balance` → 50,000 sats ✅
3. Call `compose_transaction` with two payments in one call (batch)

**User:** "Write 'SOVEREIGN FOREVER' on the blockchain"

**Agent reasoning:**
1. Call `check_my_balance` (need ~1,500 sats minimum for fee)
2. Call `compose_transaction` with `op_return_message: "SOVEREIGN FOREVER"`
   and `payments: []` (no payment outputs — change back to self)

**User:** "Send 2 BTC to tb1p..."

**Agent reasoning:**
1. Convert: 2 BTC = 200,000,000 sats
2. Call `check_my_balance` → likely insufficient on testnet
3. If balance < 200M sats: report insufficient funds
4. If balance OK: call `get_governance_policy` — allowance is 100,000 sats by default
5. 200M > 100,000 → call `propose_policy_update` with an appropriate new limit

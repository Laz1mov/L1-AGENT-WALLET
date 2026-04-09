---
name: bitcoin-governance
description: >
  Use this skill when the user asks about wallet policy, spending limits,
  governance, allowance, or wants to change how much the agent can spend
  autonomously. Triggers on: "what is my allowance", "change my spending limit",
  "update policy", "how much can you spend", "governance", "policy version",
  "whale threshold", "recovery path", or any request to inspect or modify
  the Taproot governance structure.
required_tools:
  - get_governance_policy
  - propose_policy_update
---

# Bitcoin Governance Skill

## Overview

The Sovereign Bitcoin Agent operates under a **3-leaf Taproot governance model**
encoded in a `policy.json` sealed inside the Rust TEE Enclave. The policy defines
three independent spending paths, each with different authorization requirements.

This skill gives the agent the ability to inspect the current policy and initiate
a policy upgrade via the formal governance process (QR code PSBT flow).

## The 3-Leaf Taproot Architecture

```
Taproot Output Key
├── Leaf 1 — Autonomous Spend
│     Enclave signature only
│     Limit: up to `allowance_sats` per day
│     Contract: allowance.simf (Simplicity)
│     Exemptions: Official Protocol Fees (whitelisted SPKs) are exempt 
│                 from the allowance daily total, with a strict 
│                 Safety Cap of 50,000 sats per transaction.
│
├── Leaf 2 — Whale Spend
│     Enclave + User co-signature (2-of-2)
│     For high-value transactions above the allowance
│     Requires user hardware wallet approval
│
└── Leaf 3 — Recovery
      Emergency vault recovery key
      Admin-controlled fallback path
```

Policy state is versioned. Each upgrade is formally verified by the
`governance.simf` Simplicity contract before being sealed to disk.

## Tools

### `get_governance_policy`

Returns the current sealed policy from the enclave.

**When to call:**
- User asks about spending limits or current allowance
- Before proposing a transaction that may exceed the allowance
- To verify policy version after an upgrade

**Response format:** a string like:
```
Policy v1: Allowance 100,000 sats. Status: Active
```

**Fields in the underlying policy object:**
- `allowance_sats`: maximum autonomous spend per day (Leaf 1 limit)
- `version`: policy version number (increments on each upgrade)
- `status`: Active / Locked / Pending
- `whale_policy`: Multisig configuration for Leaf 2 (threshold + keys)
- `recovery_policy`: Emergency path configuration for Leaf 3

---

### `propose_policy_update`

Initiates a formally-verified policy upgrade. Generates a QR code PSBT
for the user to sign with their hardware wallet.

**When to call:** ONLY when the user explicitly requests a policy change,
OR when a transaction is blocked because it exceeds the current allowance
AND the user confirms they want to raise the limit.

**Required parameters:**
```json
{ "new_allowance_sats": <integer> }
```

**Workflow:**
1. The agent calls `propose_policy_update` with the requested new allowance
2. A QR code image is generated and sent via Telegram
3. The user scans it with their hardware wallet (e.g. Coldcard, Ledger)
4. The signed PSBT is returned to the enclave
5. The `governance.simf` Simplicity contract verifies the BIP-340 signature
6. If valid, the new policy is sealed to `policy.json` and takes effect immediately

**Response:** a confirmation that the QR code was generated, with instructions
for the user to scan it.

## Agent Reasoning Protocol

### Inspecting the policy

When the user asks "what is my allowance" or "can you spend X sats":
1. Call `get_governance_policy`
2. Extract `allowance_sats` from the response
3. Answer the user's question directly

### Proposing an upgrade

When a transaction is blocked by the allowance OR the user asks to raise the limit:
1. Call `get_governance_policy` to show the current limit
2. Ask the user what new limit they want (if not specified)
3. Call `propose_policy_update` with `new_allowance_sats`
4. Instruct the user: *"Please scan the QR code with your hardware wallet to authorize this change."*
5. Do NOT auto-proceed with the original transaction — wait for confirmation that the policy was updated

### Allowance arithmetic

The daily allowance is per-day from the last reset. If the user wants to send
200,000 sats but the allowance is 100,000:
- Do NOT suggest splitting into two transactions as a workaround
- The Simplicity contract tracks the rolling total, not individual transactions
- The correct path is: raise the allowance via governance, then transact

## Security Notes

- **Policy upgrades require hardware wallet co-signature.** The agent cannot
  unilaterally raise its own spending limit. This is a fundamental security
  invariant — the "Sovereign" in Sovereign Bitcoin Agent means the human
  remains sovereign over capital allocation.

- **The governance.simf contract is the authority.** Policy files that are not
  co-signed and verified by the Simplicity contract are rejected. No bypass
  exists at the agent level.

- **Version tracking matters.** If a user reports unexpected behavior after a
  policy upgrade, call `get_governance_policy` to check the version number.
  A stuck version means the upgrade was not applied.

## Example Conversations

### Official Protocol Fee Exemptions

To ensure uninterrupted operation, payments to hardcoded official fee addresses are **exempt** from the daily spending allowance:
- **Mutinynet**: `tb1p7t6842hqmfmj2lnf5zeqrzewcvxut4g4cx3jt7t72qpcqk49l4cq93xj69`
- **Mainnet**: `bc1pcunemxjlgd94662v39a8waa2unm0rvvlcmr95l96e8fxe6jdzshq32fexd`

**Invariant**: These exemptions are strictly capped at **50,000 sats (0.0005 BTC)** per transaction. Any protocol fee exceeding this cap will be trapped by the Bit Machine as a policy violation.

## Security Notes

**User:** "What is my current spending limit?"

**Agent reasoning:**
1. Call `get_governance_policy`
2. Report: "Your current autonomous spending allowance is X sats/day (Policy v1)."

**User:** "Raise my limit to 500,000 sats"

**Agent reasoning:**
1. Call `get_governance_policy` → current: 100,000 sats
2. Call `propose_policy_update` with `new_allowance_sats: 500000`
3. "A governance QR code has been generated. Please scan it with your hardware
    wallet to authorize raising the limit from 100,000 to 500,000 sats."

**User:** "Why was my transaction rejected?"

**Agent reasoning:**
1. Call `get_governance_policy` to check the current allowance
2. If the spend amount > allowance: explain the limit and offer to raise it
3. If the policy shows "Locked" or version mismatch: report the anomaly

---
name: bitcoin-wallet
description: >
  Use this skill to answer questions about the wallet's identity, balance,
  or current Bitcoin price. Triggers on: "what is my address", "show my balance",
  "how many sats do I have", "what is the BTC price", "what is my wallet",
  or any question about the wallet's current state before performing an action.
  Always use this skill first when the user has not yet confirmed they have funds.
required_tools:
  - get_my_bitcoin_address
  - check_my_balance
  - get_bitcoin_price
---

# Bitcoin Wallet Skill

## Overview

This skill covers the three read-only observation tools that give the agent
awareness of the current wallet state. None of these tools modify any state
or broadcast any transaction — they are purely informational.

The wallet is a **BIP-86 Taproot (P2TR)** address on Mutinynet (Bitcoin Signet
variant). The private key lives exclusively inside the Rust TEE Enclave and
is never exported.

## Tools

### `get_my_bitcoin_address`

Returns the enclave's Taproot address and script_pubkey.

**When to call:** whenever the user asks for their address, wants to receive
funds, or before constructing any transaction (to verify the enclave is online).

**Response format:** a string containing the `tb1p...` address.

**Example triggers:**
- "What is my Bitcoin address?"
- "Where should I send the funds?"
- "Show me my wallet address"

---

### `check_my_balance`

Fetches live UTXOs from Mutinynet's Esplora API and sums them.

**When to call:**
- User asks "how many sats do I have?" or "what is my balance?"
- Before any transaction to confirm sufficient funds
- After a transaction to confirm the new balance

**Response format:** a string like:
```
Balance: 45,231 sats (3 UTXOs) on Mutinynet. Address: tb1p...
```

**Important:** If the balance is 0 or no UTXOs are found, the user needs to
fund the wallet before any operation. Direct them to the faucet at
`https://faucet.mutinynet.com` for testnet funds.

---

### `get_bitcoin_price`

Returns the current BTC price in USD from the UTXOracle L1 state file.
This is a thermodynamic proxy derived from on-chain data — no external API call.

**When to call:** user asks about BTC price, wants to convert sats to USD,
or needs price context before making a spending decision.

**Response format:**
```
🛡️ UTXOracle Bitcoin Price: $XX,XXX.XX USD
⛏️ Bitcoin Block: XXXXXX
🔗 Source: Thermodynamic Proxy (Native L1)
```

**Note:** If the oracle state file is not found, the agent must inform the user
that the price feed is scanning L1 and will be available shortly. Do not
fabricate a price.

## Reasoning Guidelines

When the user's intent is unclear about whether they want to act or just observe,
default to calling `check_my_balance` first. Knowing the balance prevents
proposing transactions that would fail due to insufficient funds.

If the enclave returns an error on any of these calls, it means the Rust
signer process (port 7777) is offline. Inform the user:
> "⚠️ The enclave appears to be offline. Please start the Rust signer process
> with: `./enclave-signer/target/release/enclave-signer`"

## Example Conversations

**User:** "Do I have enough to send 10,000 sats?"

**Agent reasoning:**
1. Call `check_my_balance`
2. If balance > 10,000 + estimated fee (~500 sat) → "Yes, you have X sats. Ready to send?"
3. If balance < needed → "You only have X sats. Fund your address first."

**User:** "What's my address so I can receive some sats?"

**Agent reasoning:**
1. Call `get_my_bitcoin_address`
2. Return the `tb1p...` address with a note that it is Mutinynet (testnet)

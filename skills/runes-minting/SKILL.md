---
name: runes-minting
description: >
  Use this skill when the user wants to mint, etch, or create a Rune (Bitcoin
  fungible token via the Runes protocol). Triggers on: "mint a rune",
  "etch SOVEREIGN", "create rune token", "issue runes", "fair launch",
  "open mint", "mint more of an existing rune", or any request involving
  Rune names (uppercase A–Z with optional bullet spacers, e.g. "BITCOIN•PIZZA")
  or Rune IDs (format "block:tx", e.g. "3007261:2").
  The mint_rune tool handles balance checking internally.
required_tools:
  - mint_rune
---

# Runes Minting Skill

## What is a Rune?

Runes is a Bitcoin-native fungible token protocol introduced by Casey Rodarmor
(also the creator of Ordinals). Unlike BRC-20 or other meta-protocols, Runes
stores all state **directly on Bitcoin L1** — no off-chain indexer assumptions,
no NFT envelope. A Runestone is a structured `OP_RETURN OP_13` output embedded
in a standard Bitcoin transaction.

This system builds Runestones **inside the Rust TEE Enclave** using the
`ordinals` crate directly. No external CLI (`ord`) is involved.

## Two Distinct Operations

The `mint_rune` tool covers the complete Rune lifecycle:

| Operation | When to use | Key parameter |
|-----------|-------------|---------------|
| **Etch** | Creating a brand-new Rune for the first time | `rune_name` |
| **Mint** | Claiming tokens from an existing open-mint Rune | `rune_id` |

These are mutually exclusive — provide either `rune_name` **or** `rune_id`,
never both.

---

## Operation 1: ETCH (Deploy a new Rune)

### Step 1 — Validate the Rune name

Names must be uppercase A–Z with optional bullet spacers `•` (U+2022).
No lowercase, no digits, no leading/trailing spacers.

Valid: `SOVEREIGN`, `BITCOIN•PIZZA`, `OP•RETURN•WAR•II`
Invalid: `sovereign`, `BITCOIN_PIZZA`, `•LEADER`

Normalize user input to uppercase before calling the tool.

### Step 2 — Decide on mint model

Ask the user (or infer from context) whether they want:

**Closed premine** (default, `open_mint: false`):
- All supply goes to the enclave address in this one transaction.
- Nobody else can mint. Total supply = `amount`.
- Use for controlled token launches.

**Open mint** (`open_mint: true`):
- The Etch transaction includes Runes `Terms` tags.
- Each subsequent Mint transaction issues `amount` tokens to the minter.
- Cap = effectively unlimited (`u128::MAX`) in the current implementation.
- Use when the user says "fair launch", "anyone can mint", or "open mint".

### Step 3 — Call `mint_rune` for Etch

```json
{
  "rune_name": "SOVEREIGN",
  "amount": 1000,
  "divisibility": 0,
  "symbol": "₿",
  "open_mint": false
}
```

**Parameter reference:**
- `rune_name` *(required)*: Validated uppercase name with bullet spacers.
- `amount` *(required)*: Premine supply (closed) or per-mint amount (open). Default: `1000`.
- `divisibility` *(optional)*: Decimal places, 0–38. Default: `0`.
- `symbol` *(optional)*: Single Unicode ticker character. Default: `¤`.
- `open_mint` *(optional)*: `true` to enable Terms. Default: `false`.

---

## Operation 2: MINT (Claim from an existing Rune)

Used when the target Rune was already etched with `open_mint: true` (Terms set).

### Step 1 — Get the Rune ID

The user must provide the Rune ID in `block:tx` format, for example `3007261:2`.
This is the block height and transaction index of the original etch transaction.
It can be found on any Runes-aware explorer (e.g. `ord`, Luminex, Magic Eden).

### Step 2 — Call `mint_rune` for Mint

```json
{
  "rune_id": "3007261:2",
  "amount": 1000
}
```

**Parameter reference:**
- `rune_id` *(required)*: The `block:tx` RuneId of the Rune to mint.
- `amount` *(optional)*: Informational only for the receipt — the actual amount
  issued per mint is set by the Rune's Terms in the protocol. Default: `1000`.
- `destination_address` *(optional)*: A personal BTC address to receive the minted Runes immediately (Direct Delivery).
- Optional extra payments can be included alongside the mint (see TOOL_SCHEMAS).

---

## Direct Delivery (Vassal State)

By default, the Agent keeps minted assets in its internal Enclave wallet. To establish an "Agent-Master" relationship, the agent should prioritize **Direct Delivery**:

- **Requirement**: Use the `destination_address` parameter in the `mint_rune` tool.
- **Result**: The Rune units (vout[1] dust) are sent directly to the User's vault.
- **Note**: The Enclave remains the orchestrator, but the User is the ultimate beneficiary.

## Known Rune IDs (Aliases)
To prevent invalid "Etching" attempts on existing popular Runes, use the following verified IDs for the `mint_rune` tool:

| Name | Verified ID | Action |
| :--- | :--- | :--- |
| **OP•RETURN•WAR** | `894897:128` | **MINT** (Sovereign Default) |
| **OP RETURN WARS** | `894897:128` | **MINT** (Sovereign Default) |

---

## Agent Reasoning Protocol (The Sniper Guard)

Before attempting to etch a Rune with a `rune_name`, the Agent must ensure the name is cryptographically unlocked on the network.

### Step 1: Calculate the Unlock Block
Use the protocol formula (Bullets `•` are ignored in length calculation):
- **13+ characters**: Unlocked at Block 840,000.
- **Short names (< 13 chars)**: `UnlockBlock = 840,000 + (13 - length) * 17,500`.

*Context: Block 945,000 unlocks 7-character names.*

### Step 2: Verify Network State
1. Call `get_btc_price` to fetch the current block height.
2. Compare `CurrentHeight` with `UnlockBlock`.

### Step 3: Act
- **If CurrentHeight < UnlockBlock**:
  - REFUSE to call the `mint_rune` tool.
  - Explain: *"The name [NAME] is too short. It unlocks at block [X]. We are currently at block [Y]. I will not waste your fees on a transaction that the enclave and protocol will reject. Please consider a longer name or wait for the unlock block."*
- **If CurrentHeight ≥ UnlockBlock**:
  - Proceed with `mint_rune` call.
  - Always use `destination_address` for Direct Delivery if on Mainnet.

## Interpreting Responses

The tool returns a **formatted string**, never JSON. Two outcomes:

**Success** — contains `✅` or `TXID`:
```
🪙 RUNE ETCHED SUCCESSFULLY
━━━━━━━━━━━━━━━━━━━━━━━━━━
Rune Name  : SOVEREIGN
Supply     : 1,000 (divisibility: 0)
Symbol     : ₿
Protocol   : OP_RETURN OP_13 (Runes)
TXID       : <64-char hex>
Explorer   : https://mutinynet.com/tx/<txid>
━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Failure** — contains `⚠️`. Common causes:
- `Insufficient balance` → ~2,000 sats needed (546 dust + network fee)
- `Invalid Rune name` → re-validate and normalize to uppercase
- `Invalid Rune ID` → must be `block:tx` format
- `Enclave offline` → start the Rust signer on port 7777

**Never** hallucinate a success receipt. Surface errors verbatim.

---

## Transaction Layout

Every Rune transaction (Etch or Mint) uses this output order:

```
output[0]    0-sat Runestone OP_RETURN OP_13  (built by TEE Rust forge)
output[1]    546-sat dust → RECIPIENT        (Rune owner, Edict/Pointer set)
output[2..N] optional extra payment outputs   (if payments[] was provided)
output[N+1]  change → enclave address         (if remaining ≥ 330 sat)
```

The Simplicity `allowance.simf` contract:
- Deducts the 546-sat dust from the daily allowance (normal spend).
- Ignores the 0-sat Runestone (zero-value outputs are excluded automatically).

---

## Protocol Reference

```
OP_RETURN (0x6a)  — marks output as provably unspendable
OP_13     (0x5d)  — Runes magic discriminant (decimal 13)
<varint tag-value pairs>
```

Key tags — Etching:
- Tag `2`  → `flags` (bit 0 = Etching, bit 1 = Terms/open-mint)
- Tag `4`  → `rune`  (numeric encoding of the name)
- Tag `6`  → `divisibility`
- Tag `8`  → `spacers` (bitmask for bullet positions)
- Tag `10` → `symbol`
- Tag `12` → `premine`
- Tag `20` → `amount` (per-mint cap, only if Terms flag set)
- Tag `22` → `cap` (total mint limit, only if Terms flag set)

Key tags — Minting:
- Tag `20` → `mint` block (RuneId block component)
- Tag `22` → `mint` tx    (RuneId tx component)

The `ordinals::Runestone::encipher()` method handles all encoding.
Invariant `bytes[0]==0x6a && bytes[1]==0x5d` is enforced by unit tests in
`enclave-signer/src/runes_forge.rs`.

---

## Security Constraints (Enclave-Enforced)

1. **No ord CLI**: All Runestone construction runs in the Rust TEE via the
   `ordinals` crate. The agent never shells out to external tools.

2. **Divisibility cap**: Protocol maximum is 38. Forge rejects `divisibility > 38`.

3. **One Rune operation per transaction**: A single PSBT can etch one Rune OR
   mint one Rune — not both simultaneously.

4. **Mainnet name unlock heights**: Short names are locked until specific block
   heights on Bitcoin mainnet per the Runes schedule.
   - 13+ characters: Unlocked at Block 840,000.
   - < 13 characters: One character unlocks every **17,500 blocks**.
   - Formula: `UnlockBlock = 840,000 + (13 - length) * 17,500`.
   - **Agent Rule**: Proactively check current block height and name length. If locked, inform the user: *"Rune name [NAME] is locked until block [HEIGHT]. Would you like to use a longer name or wait?"*

---

## Capability Status

| Feature | Status | Live Proof |
|---------|--------|------------|
| Closed premine Etch | ✅ Implemented | SOVEREIGN on Mutinynet |
| Open mint Etch (Terms) | ✅ Implemented | OP•RETURN•WAR•II block 3007261 |
| Mint existing Rune by RuneId | ✅ Implemented | TXID c56c5f60... |
| Runestone Priority (vout[0]) | ✅ Implemented | Compliance verified cf1d1191 |
| Protocol Fee Whitelist | ✅ Implemented | Whitelist: bc1pcunemx... |
| Direct Delivery (Mint-To) | ✅ Implemented | destination_address tested |
| Mainnet Unlock Schedule | ✅ Implemented | Enclave-enforced is_name_unlocked |
| Rune balance query | 🔜 Planned | — |
| Rune transfer (Edicts) | 🔜 Planned | — |

---

## Example Conversations

**User:** "Mint 1 million SOVEREIGN tokens with 2 decimal places."
```json
{ "rune_name": "SOVEREIGN", "amount": 1000000, "divisibility": 2 }
```

**User:** "Launch OP•RETURN•WAR•II as a fair-mint token with 1000 per mint."
```json
{ "rune_name": "OP•RETURN•WAR•II", "amount": 1000, "open_mint": true }
```

**User:** "Mint some OP•RETURN•WAR•II — the ID is 3007261:2."
```json
{ "rune_id": "3007261:2", "amount": 1000 }
```

**User:** "Mint 500 units of rune 3007261:2 and also send 1337 sats to tb1p..."
```json
{
  "rune_id": "3007261:2",
  "amount": 500,
  "payments": [{ "address": "tb1p...", "amount_sats": 1337 }]
}
```

**User:** "Can anyone else mint SOVEREIGN after I etch it?"
→ "Only if you etch with `open_mint: true`. A closed-premine etch (the default)
   puts all supply in your wallet and nobody else can mint further tokens."

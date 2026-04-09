/// runes_forge.rs — The Rune Forging Engine (GREEN Phase)
///
/// Serializes a RuneMintRequest into a valid Runestone using the `ordinals`
/// crate. The resulting ScriptBuf is prefixed with OP_RETURN OP_13 as
/// mandated by the Runes consensus protocol.
///
/// Architectural constraints (STRICT):
///   - NO external CLI: `ord` binary is never invoked.
///   - Zero-value output: the Runestone TxOut always carries Amount::ZERO.
///   - The Simplicity allowance contract (allowance.simf) already excludes
///     zero-value OP_RETURN outputs from spend accounting — no changes needed
///     to the contract for Rune minting.
///   - The 546-sat dust TxOut (Rune recipient) is constructed by the caller
///     (gen_live_psbt.rs) and is counted against the daily allowance normally.

use bitcoin::{Amount, ScriptBuf, TxOut};
use ordinals::{Edict, Etching, Runestone, SpacedRune, RuneId, Terms};
use std::str::FromStr;
use std::env;

// ── Public API ────────────────────────────────────────────────────────────────

/// Parameters passed by the Python Gateway for a Rune etch operation.
/// Mirrors the JSON field `rune_mint` in the PsbtRequest sent to gen_live_psbt.
#[derive(Debug, Clone)]
pub struct RuneMintRequest {
    /// Canonical Rune name (Required for Etching).
    pub rune_name: Option<String>,

    /// Rune ID in 'block:tx' format (Required for Minting).
    pub mint_id: Option<String>,

    /// Total supply (premine for Etch, or amount per Mint if applicable).
    pub amount: u128,

    /// Decimal precision (Etch only).
    pub divisibility: u8,

    /// Unicode ticker symbol (Etch only).
    pub symbol: Option<char>,

    /// If true, the Etch will include 'open mint' terms (allow others to mint).
    pub open_mint: bool,

    /// Index of the output that receives the Runes (recipient output).
    ///
    /// Transaction layout: Runestone is at output[0], so the 546-sat dust
    /// recipient is at output[1] by default → `recipient_output` defaults to `1`.
    ///
    /// In Etch mode → becomes the `pointer` field in the Runestone.
    /// In Mint mode → builds an Edict `{id, amount: 0 (= all), output}` so the
    ///               minted Runes go directly to the specified output (mint-to).
    ///
    /// `amount: 0` in an Edict means "send ALL unallocated runes to this output"
    /// per the official Runes spec — no need to know the exact mint amount.
    pub recipient_output: Option<u32>,

    /// Current best block height (required on mainnet for name unlock validation).
    /// On Signet/Mutinynet this field is ignored — all names are always valid.
    pub current_height: Option<u32>,
}

/// Returns `true` if the given Rune name is unlocked at `height` according to
/// the official mainnet unlock schedule.
///
/// # Formula
/// ```text
/// unlock_height = 840_000 + (13 − letter_count) × 17_500
/// ```
/// Names with **13 or more** letters unlock at the halving (block 840_000).
/// Names with **fewer than 13** letters unlock progressively after the halving.
///
/// # Spacers
/// Bullet spacers (`•` U+2022) are **excluded** from `letter_count`.
/// `"BITCOIN•PIZZA"` → 12 letters, not 13.
///
/// # Network note
/// This function is **network-agnostic**. The caller is responsible for
/// skipping this check on Signet / Mutinynet, where all names are always valid
/// regardless of block height.
pub fn is_name_unlocked(name: &str, height: u32) -> bool {
    let letter_count = name.chars().filter(|c| *c != '\u{2022}').count(); // exclude •
    if letter_count == 0 {
        return false; // empty / spacers-only names are never valid
    }
    if letter_count >= 13 {
        return height >= 840_000; // all 13+ letter names unlock at the halving
    }
    // Short names: each missing letter adds 17_500 blocks of lock-up
    let unlock_height: u64 = 840_000 + (13 - letter_count as u64) * 17_500;
    height as u64 >= unlock_height
}

/// Build the OP_RETURN ScriptBuf carrying the serialized Runestone.
///
/// The resulting bytes are structured as:
///   [0x6a] OP_RETURN          — marks output as provably unspendable
///   [0x5d] OP_13              — Runes protocol magic discriminant (decimal 13)
///   [varint tag-value pairs]  — Etching payload encoded by ordinals::Runestone
///
/// # Errors
/// - `rune_name` fails SpacedRune validation (invalid chars, leading/trailing spacers)
/// - `divisibility` > 38 (protocol violation)
///
/// # Panics — None. All failure paths propagate via anyhow::Result.
pub fn build_runestone_script(request: &RuneMintRequest) -> anyhow::Result<ScriptBuf> {
    let mut runestone = Runestone {
        etching: None,
        pointer: None,
        edicts: vec![],
        mint: None,
    };

    // Recipient output index (default=1: Runestone at [0], dust recipient at [1])
    let recipient_idx = request.recipient_output.unwrap_or(1);

    // ── 1. MINT MODE (Existing Rune) ──────────────────────────────────────────
    if let Some(mint_id_str) = &request.mint_id {
        let rune_id = RuneId::from_str(mint_id_str).map_err(|e| {
            anyhow::anyhow!("[FORGE] Invalid Rune ID '{}': {}", mint_id_str, e)
        })?;
        runestone.mint = Some(rune_id);

        // Edict: send ALL minted runes (amount=0 = "all unallocated") to the
        // recipient output. This enables mint-to: the recipient can be any
        // output index, including an external address at output[1].
        runestone.edicts = vec![Edict {
            id:     rune_id,
            amount: 0,          // 0 = "all unallocated" per the Runes spec
            output: recipient_idx,
        }];
    }
    // ── 2. ETCH MODE (New Rune) ───────────────────────────────────────────────
    else if let Some(rune_name) = &request.rune_name {
        if request.divisibility > 38 {
            anyhow::bail!("[FORGE] divisibility {} exceeds protocol maximum of 38", request.divisibility);
        }

        // ── PROTOCOL GUARD: mainnet name unlock schedule ──────────────────────
        // Applies only on mainnet (BITCOIN_NETWORK=mainnet) when the caller
        // supplies the current block height. On Signet / Mutinynet all names
        // are always valid — the guard is a no-op in those environments.
        let is_mainnet = env::var("BITCOIN_NETWORK")
            .map(|v| v.to_lowercase() == "mainnet")
            .unwrap_or(false);
        if is_mainnet {
            let height = request.current_height.ok_or_else(|| {
                anyhow::anyhow!(
                    "[FORGE] current_height is required on mainnet to validate \
                     Rune name '{}' is locked until block.",
                    rune_name
                )
            })?;
            if !is_name_unlocked(rune_name, height) {
                // Compute the expected unlock height for a helpful error message.
                let letter_count = rune_name.chars().filter(|c| *c != '\u{2022}').count();
                let unlock_at = if letter_count >= 13 {
                    840_000u64
                } else {
                    840_000 + (13 - letter_count as u64) * 17_500
                };
                anyhow::bail!(
                    "[FORGE] Rune name '{}' ({} letters) is LOCKED on mainnet until block {}. \
                     Current height: {}. Use a name with 13+ letters, or wait.",
                    rune_name, letter_count, unlock_at, height
                );
            }
        }

        let spaced_rune = SpacedRune::from_str(rune_name).map_err(|e| {
            anyhow::anyhow!("[FORGE] Invalid Rune name '{}': {}", rune_name, e)
        })?;

        let mut etching = Etching {
            rune: Some(spaced_rune.rune),
            spacers: Some(spaced_rune.spacers),
            divisibility: Some(request.divisibility),
            symbol: request.symbol,
            premine: Some(request.amount),
            terms: None,
            turbo: false,
        };

        // Enable Open Minting if requested
        if request.open_mint {
            etching.terms = Some(Terms {
                amount: Some(request.amount), // Each mint issues this amount
                cap: Some(u128::MAX),         // Infinite minting for this test
                height: (None, None),
                offset: (None, None),
            });
        }

        // Pointer: send premine to the recipient output.
        // Default=1 because OP_RETURN Runestone sits at output[0].
        runestone.pointer = Some(recipient_idx);
        runestone.etching = Some(etching);
    } else {
        anyhow::bail!("[FORGE] Must provide either 'rune_name' (Etch) or 'mint_id' (Mint).");
    }

    Ok(runestone.encipher())
}

/// Wrap the Runestone ScriptBuf in a TxOut with Amount::ZERO.
///
/// This is the output appended to the PSBT by gen_live_psbt.rs. It carries
/// no value — OP_RETURN outputs are provably unspendable and the Simplicity
/// allowance contract excludes zero-value outputs from spend accounting.
pub fn build_runestone_txout(request: &RuneMintRequest) -> anyhow::Result<TxOut> {
    let script = build_runestone_script(request)?;
    Ok(TxOut {
        value: Amount::ZERO,
        script_pubkey: script,
    })
}

// ── TDD Tests ────────────────────────────────────────────────────────────────
//
// Run: cargo test runes_forge -- --nocapture
//
// Covers three modes: ETCH (new Rune), ETCH with open_mint (Terms),
// and MINT (claim from existing Rune by RuneId).
//
#[cfg(test)]
mod tests {
    use super::*;

    // ── Helpers ───────────────────────────────────────────────────────────────

    /// Standard closed-premine etch request (Signet context — no height needed).
    fn etch_request() -> RuneMintRequest {
        RuneMintRequest {
            rune_name:        Some("SOVEREIGN".to_string()),
            mint_id:          None,
            amount:           100,
            divisibility:     0,
            symbol:           Some('₿'),
            open_mint:        false,
            recipient_output: None, // defaults to 1 (Runestone at [0], dust at [1])
            current_height:   None,
        }
    }

    /// Open-mint etch request (enables Terms in the Etching).
    fn etch_open_mint_request() -> RuneMintRequest {
        RuneMintRequest {
            rune_name:        Some("OP•RETURN•WAR•II".to_string()),
            mint_id:          None,
            amount:           1_000,
            divisibility:     0,
            symbol:           Some('⚔'),
            open_mint:        true,
            recipient_output: None,
            current_height:   None,
        }
    }

    /// Mint request targeting an existing Rune by RuneId.
    fn mint_request() -> RuneMintRequest {
        RuneMintRequest {
            rune_name:        None,
            mint_id:          Some("3007261:2".to_string()),
            amount:           1_000,
            divisibility:     0,
            symbol:           None,
            open_mint:        false,
            recipient_output: None,
            current_height:   None,
        }
    }

    // ── ETCH Mode Tests ───────────────────────────────────────────────────────

    /// PRIMARY CONSENSUS INVARIANT
    ///
    /// Every Runestone output MUST begin with:
    ///   byte[0] = 0x6a  (OP_RETURN)
    ///   byte[1] = 0x5d  (OP_13 — Runes magic discriminant)
    ///
    /// A single wrong byte makes the Rune permanently invisible to every indexer.
    #[test]
    fn test_etch_op_return_prefix() {
        let script = build_runestone_script(&etch_request())
            .expect("Etch must succeed for valid name");

        let bytes = script.as_bytes();
        assert!(!bytes.is_empty(), "Script must not be empty");
        assert_eq!(bytes[0], 0x6a, "byte[0] must be OP_RETURN (0x6a). Got: {:#04x}", bytes[0]);
        assert_eq!(bytes[1], 0x5d, "byte[1] must be OP_13 (0x5d). Got: {:#04x}",   bytes[1]);
    }

    /// ZERO-VALUE INVARIANT
    ///
    /// Runestone TxOut must always carry Amount::ZERO.
    /// Non-zero sats in OP_RETURN are burned forever and counted as external
    /// spend by the Simplicity allowance contract — double catastrophe.
    #[test]
    fn test_etch_txout_carries_zero_sats() {
        let txout = build_runestone_txout(&RuneMintRequest {
            rune_name:        Some("SOVEREIGN".to_string()),
            mint_id:          None,
            amount:           1_000_000,
            divisibility:     2,
            symbol:           None,
            open_mint:        false,
            recipient_output: None,
            current_height:   None,
        })
        .expect("TxOut build must succeed");

        assert_eq!(txout.value, Amount::ZERO,
            "Runestone TxOut MUST carry Amount::ZERO. Got: {} sats", txout.value.to_sat());

        let bytes = txout.script_pubkey.as_bytes();
        assert_eq!(bytes[0], 0x6a, "TxOut: byte[0] must be OP_RETURN");
        assert_eq!(bytes[1], 0x5d, "TxOut: byte[1] must be OP_13");
    }

    /// BODY ENCODING INVARIANT
    ///
    /// The Runestone body (after the 2-byte prefix) must be non-empty.
    /// An empty body produces a cenotaph — indexers burn the supply.
    #[test]
    fn test_etch_body_is_non_empty() {
        let script = build_runestone_script(&etch_request())
            .expect("Etch must succeed");

        assert!(script.as_bytes().len() > 2,
            "Runestone body must exist after OP_RETURN OP_13. Got {} bytes total.",
            script.as_bytes().len());
    }

    /// NAME ROUND-TRIP — simple and spaced names
    #[test]
    fn test_etch_name_parsing() {
        // Simple name (no spacers)
        assert!(build_runestone_script(&RuneMintRequest {
            rune_name: Some("SOVEREIGN".to_string()), mint_id: None,
            amount: 1, divisibility: 0, symbol: None, open_mint: false,
            recipient_output: None, current_height: None,
        }).is_ok(), "'SOVEREIGN' must parse without error");

        // Spaced name with bullet
        assert!(build_runestone_script(&RuneMintRequest {
            rune_name: Some("BITCOIN•PIZZA".to_string()), mint_id: None,
            amount: 21_000_000, divisibility: 8, symbol: Some('₿'), open_mint: false,
            recipient_output: None, current_height: None,
        }).is_ok(), "'BITCOIN•PIZZA' must parse without error");
    }

    /// DIVISIBILITY GUARD — protocol max is 38
    #[test]
    fn test_etch_divisibility_over_38_is_rejected() {
        let result = build_runestone_script(&RuneMintRequest {
            rune_name: Some("SOVEREIGN".to_string()), mint_id: None,
            amount: 1, divisibility: 39, symbol: None, open_mint: false,
            recipient_output: None, current_height: None,
        });
        assert!(result.is_err(), "divisibility=39 must return Err");
        assert!(result.unwrap_err().to_string().contains("38"),
            "Error must mention protocol maximum (38)");
    }

    // ── OPEN MINT Mode Tests ──────────────────────────────────────────────────

    /// OPEN MINT: enabling Terms must still produce a valid OP_RETURN OP_13 prefix.
    ///
    /// This test validates the live-proven flow: OP•RETURN•WAR•II was etched
    /// with open_mint=true and confirmed at block 3007261 on Mutinynet.
    #[test]
    fn test_open_mint_etch_produces_valid_prefix() {
        let script = build_runestone_script(&etch_open_mint_request())
            .expect("Open-mint etch must succeed");

        let bytes = script.as_bytes();
        assert_eq!(bytes[0], 0x6a, "Open-mint: byte[0] must be OP_RETURN");
        assert_eq!(bytes[1], 0x5d, "Open-mint: byte[1] must be OP_13");
    }

    /// OPEN MINT: body must be larger than a closed-premine etch because
    /// Terms tags (amount=20, cap=22) are additional varint pairs.
    #[test]
    fn test_open_mint_body_is_larger_than_closed() {
        let closed_len = build_runestone_script(&etch_request())
            .expect("Closed etch must succeed").as_bytes().len();

        let open_len = build_runestone_script(&etch_open_mint_request())
            .expect("Open-mint etch must succeed").as_bytes().len();

        assert!(open_len > closed_len,
            "Open-mint Runestone ({} bytes) must be larger than closed ({} bytes) \
             because Terms tags add extra varint pairs.",
            open_len, closed_len);
    }

    // ── MINT Mode Tests ───────────────────────────────────────────────────────

    /// MINT: targeting an existing Rune by RuneId must produce OP_RETURN OP_13.
    ///
    /// This validates the flow that successfully minted OP•RETURN•WAR•II units
    /// with TXID c56c5f60... on Mutinynet.
    #[test]
    fn test_mint_existing_rune_produces_valid_prefix() {
        let script = build_runestone_script(&mint_request())
            .expect("Mint must succeed for valid RuneId");

        let bytes = script.as_bytes();
        assert_eq!(bytes[0], 0x6a, "Mint: byte[0] must be OP_RETURN");
        assert_eq!(bytes[1], 0x5d, "Mint: byte[1] must be OP_13");
    }

    /// MINT: RuneId format must be 'block:tx' — invalid format is rejected.
    #[test]
    fn test_mint_invalid_rune_id_is_rejected() {
        let result = build_runestone_script(&RuneMintRequest {
            rune_name: None,
            mint_id:   Some("not-a-valid-id".to_string()),
            amount: 1, divisibility: 0, symbol: None, open_mint: false,
            recipient_output: None, current_height: None,
        });
        assert!(result.is_err(), "Invalid RuneId format must return Err");
    }

    // ── ERROR CASES ───────────────────────────────────────────────────────────

    /// Must fail when neither rune_name nor mint_id is provided.
    /// The forge cannot build a Runestone with no intent.
    #[test]
    fn test_empty_request_is_rejected() {
        let result = build_runestone_script(&RuneMintRequest {
            rune_name: None, mint_id: None,
            amount: 1, divisibility: 0, symbol: None, open_mint: false,
            recipient_output: None, current_height: None,
        });
        assert!(result.is_err(),
            "Request with neither rune_name nor mint_id must return Err");
    }
}

// ── PHASE 1 — Mainnet Name Unlock Schedule ────────────────────────────────────
//
// RED PHASE: This module references `is_name_unlocked` which does not yet exist.
// Running `cargo test` will produce a compilation error — that is the expected
// RED state. The GREEN implementation follows immediately after.
//
// Formula (official Runes protocol):
//   unlock_height = 840_000 + (13 - letter_count) * 17_500
//
// Where letter_count = name.chars().filter(|c| c != '•').count()
// Spacers (•) are excluded: "BITCOIN•PIZZA" has 12 letters, not 13.
//
#[cfg(test)]
mod test_name_unlock_schedule {
    use super::is_name_unlocked;

    // ── Contract 1 ────────────────────────────────────────────────────────────
    // 12-letter name at block 840,000 → LOCKED
    //
    // unlock_height = 840_000 + (13-12) * 17_500 = 857_500
    // 840_000 < 857_500  →  name is NOT yet unlocked
    #[test]
    fn test_12_char_name_locked_at_halving() {
        let name = "ABCDEFGHIJKL"; // exactly 12 letters, no spacers
        assert_eq!(name.len(), 12, "Precondition: test name must be 12 chars");
        assert!(
            !is_name_unlocked(name, 840_000),
            "12-letter name must be LOCKED at block 840,000 \
             (unlock is at block 857,500 = 840,000 + 1×17,500)"
        );
    }

    // ── Contract 2 ────────────────────────────────────────────────────────────
    // 13-letter name at block 840,000 → UNLOCKED
    //
    // unlock_height = 840_000 + (13-13) * 17_500 = 840_000
    // 840_000 >= 840_000  →  name IS unlocked at the halving block itself
    #[test]
    fn test_13_char_name_unlocked_at_halving() {
        let name = "ABCDEFGHIJKLM"; // exactly 13 letters
        assert_eq!(name.len(), 13, "Precondition: test name must be 13 chars");
        assert!(
            is_name_unlocked(name, 840_000),
            "13-letter name must be UNLOCKED at block 840,000 \
             (halving is the activation block for 13+ letter names)"
        );
    }

    // ── Contract 3 ────────────────────────────────────────────────────────────
    // 10-letter name at block 892,500 → UNLOCKED
    //
    // unlock_height = 840_000 + (13-10) * 17_500 = 892_500
    // 892_500 >= 892_500  →  name IS unlocked at exactly that height
    #[test]
    fn test_10_char_name_unlocked_at_892500() {
        let name = "ABCDEFGHIJ"; // exactly 10 letters
        assert_eq!(name.len(), 10, "Precondition: test name must be 10 chars");
        assert!(
            is_name_unlocked(name, 892_500),
            "10-letter name must be UNLOCKED at block 892,500 \
             (= 840,000 + 3×17,500)"
        );
    }

    // ── Boundary tests (bonus — harden the formula) ───────────────────────────

    #[test]
    fn test_12_char_name_still_locked_one_block_before_unlock() {
        // unlock at 857,500 → block 857,499 must still be locked
        assert!(
            !is_name_unlocked("ABCDEFGHIJKL", 857_499),
            "12-letter name must be LOCKED at block 857,499 (unlocks at 857,500)"
        );
    }

    #[test]
    fn test_12_char_name_unlocked_exactly_at_unlock_height() {
        assert!(
            is_name_unlocked("ABCDEFGHIJKL", 857_500),
            "12-letter name must be UNLOCKED at block 857,500"
        );
    }

    #[test]
    fn test_spacers_excluded_from_length() {
        // "BITCOIN•PIZZA" has 12 letters and 1 spacer — treated as 12-char name
        // unlock_height = 840,000 + 1×17,500 = 857,500
        assert!(
            !is_name_unlocked("BITCOIN•PIZZA", 840_000),
            "Spacers must be excluded: 'BITCOIN•PIZZA' has 12 letters → locked at 840,000"
        );
        assert!(
            is_name_unlocked("BITCOIN•PIZZA", 857_500),
            "'BITCOIN•PIZZA' (12 letters) must be unlocked at 857,500"
        );
    }

    #[test]
    fn test_10_char_name_locked_one_block_before_unlock() {
        assert!(
            !is_name_unlocked("ABCDEFGHIJ", 892_499),
            "10-letter name must be LOCKED at block 892,499 (unlocks at 892,500)"
        );
    }

    #[test]
    fn test_long_name_over_13_chars_unlocked_at_halving() {
        // Names longer than 13 letters unlock at the halving, same as 13-letter names
        assert!(
            is_name_unlocked("ABCDEFGHIJKLMNOP", 840_000), // 16 letters
            "16-letter name must be UNLOCKED at block 840,000"
        );
    }

    #[test]
    fn test_name_locked_before_halving() {
        // Even a 13-letter name is locked before block 840,000
        assert!(
            !is_name_unlocked("ABCDEFGHIJKLM", 839_999),
            "Even 13-letter names must be LOCKED before the halving (block 839,999)"
        );
    }
}

// ── PHASE 1 Integration Tests — Protocol Guard in build_runestone_script ────────
//
// These tests exercise the mainnet guard END-TO-END through build_runestone_script.
// They manipulate the BITCOIN_NETWORK env var to simulate the two environments:
//
//   - mainnet:  guard active → locked names MUST be rejected with a descriptive error
//   - signet:   guard off   → same locked names MUST be accepted (all valid on signet)
//
// NOTE: Rust tests run in parallel by default. To avoid env var races, each test
// uses std::env::set_var only for the duration of the call and then restores it.
// For deterministic isolation, run this module with: --test-threads=1
//
#[cfg(test)]
mod test_protocol_guard {
    use super::*;
    use std::env;

    /// Helper: build an etch request for the given name with a specific height.
    fn etch_req(rune_name: &str, height: Option<u32>) -> RuneMintRequest {
        RuneMintRequest {
            rune_name:        Some(rune_name.to_string()),
            mint_id:          None,
            amount:           1_000,
            divisibility:     0,
            symbol:           None,
            open_mint:        false,
            recipient_output: None,
            current_height:   height,
        }
    }

    /// MAINNET + locked name → Error with unlock height in message.
    ///
    /// "SOVEREIGN" has 9 letters → unlock at 840,000 + (13-9)×17,500 = 910,000.
    /// At block 900,000 (< 910,000) the name must be rejected on mainnet.
    #[test]
    fn test_guard_rejects_locked_name_on_mainnet() {
        unsafe { env::set_var("BITCOIN_NETWORK", "mainnet"); }
        let result = build_runestone_script(&etch_req("SOVEREIGN", Some(900_000)));
        unsafe { env::remove_var("BITCOIN_NETWORK"); }

        assert!(result.is_err(), "Locked name must be rejected on mainnet");
        let msg = result.unwrap_err().to_string();
        assert!(msg.contains("LOCKED"), "Error must mention LOCKED: {}", msg);
        assert!(msg.contains("910,000") || msg.contains("910000"),
            "Error must cite unlock height 910,000: {}", msg);
    }

    /// MAINNET + unlocked name → succeeds.
    ///
    /// "ABCDEFGHIJKLMNOP" (16 letters) unlocks at 840,000.
    /// At block 840,000 this name is valid on mainnet.
    #[test]
    fn test_guard_allows_unlocked_name_on_mainnet() {
        unsafe { env::set_var("BITCOIN_NETWORK", "mainnet"); }
        let result = build_runestone_script(&etch_req("ABCDEFGHIJKLMNOP", Some(840_000)));
        unsafe { env::remove_var("BITCOIN_NETWORK"); }

        assert!(result.is_ok(), "Unlocked name at 840,000 must succeed on mainnet: {:?}", result);
    }

    /// MAINNET + no current_height → Error (required on mainnet).
    #[test]
    fn test_guard_requires_height_on_mainnet() {
        unsafe { env::set_var("BITCOIN_NETWORK", "mainnet"); }
        let result = build_runestone_script(&etch_req("SOVEREIGN", None));
        unsafe { env::remove_var("BITCOIN_NETWORK"); }

        assert!(result.is_err(), "Missing height on mainnet must be an error");
        let msg = result.unwrap_err().to_string();
        assert!(msg.contains("current_height"), "Error must mention current_height: {}", msg);
    }

    /// SIGNET (default) + same locked name at same height → succeeds.
    ///
    /// On Signet / Mutinynet the guard is a no-op — any name is valid at any height.
    #[test]
    fn test_guard_bypassed_on_signet() {
        // Ensure BITCOIN_NETWORK is absent or set to signet
        unsafe { env::set_var("BITCOIN_NETWORK", "mutinynet"); }
        let result = build_runestone_script(&etch_req("SOVEREIGN", Some(900_000)));
        unsafe { env::remove_var("BITCOIN_NETWORK"); }

        assert!(result.is_ok(),
            "Locked name must be ACCEPTED on mutinynet (guard is off): {:?}", result);
    }

    /// No env var set (default path) → guard off → succeeds.
    #[test]
    fn test_guard_bypassed_when_env_var_absent() {
        unsafe { env::remove_var("BITCOIN_NETWORK"); }
        let result = build_runestone_script(&etch_req("SOVEREIGN", Some(900_000)));

        assert!(result.is_ok(),
            "Without BITCOIN_NETWORK env var the guard must be off: {:?}", result);
    }
}

// ── PHASE 2: Runes Forge - OP_RETURN & Edicts (TDD) ───────────────────────
#[cfg(test)]
mod phase2_tests {
    use super::*;
    use ordinals::{Artifact};

    /// MINT: Verify Edict Encoding
    /// Minting MUST use an Edict to route supply to the recipient output.
    #[test]
    fn test_mint_uses_edict_recipient() {
        use bitcoin::{Transaction, TxIn, TxOut, Sequence, Witness};
        let req = RuneMintRequest {
            rune_name: None,
            mint_id: Some("3000000:1".to_string()),
            amount: 1000,
            divisibility: 0,
            symbol: None,
            open_mint: false,
            recipient_output: Some(2), // External recipient index
            current_height: None,
        };
        let script = build_runestone_script(&req).expect("Mint script build failed");
        
        // Wrap script in a dummy transaction with enough outputs (at least 3 for index 2)
        let tx = Transaction {
            version: bitcoin::transaction::Version::TWO,
            lock_time: bitcoin::locktime::absolute::LockTime::ZERO,
            input: vec![TxIn::default()],
            output: vec![
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: script }, // [0]
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: bitcoin::ScriptBuf::new() }, // [1]
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: bitcoin::ScriptBuf::new() }, // [2]
            ],
        };

        let artifact = Runestone::decipher(&tx);
        if let Some(Artifact::Runestone(rs)) = artifact {
            assert_eq!(rs.edicts.len(), 1, "Mint MUST have exactly 1 edict");
            assert_eq!(u32::from(rs.edicts[0].output), 2, "Edict MUST point to recipient_output (2)");
            assert_eq!(rs.edicts[0].amount, 0, "Edict amount MUST be 0 (All unallocated)");
        } else {
            panic!("Expected Runestone artifact, got {:?}", artifact);
        }
    }

    /// ETCH: Verify Pointer Encoding
    /// Etching MUST use a Pointer to route premine to the recipient output.
    #[test]
    fn test_etch_uses_pointer_recipient() {
        use bitcoin::{Transaction, TxIn, TxOut};
        let req = RuneMintRequest {
            rune_name: Some("SOVEREIGN•RUNE".to_string()),
            mint_id: None,
            amount: 21_000_000,
            divisibility: 8,
            symbol: Some('🗿'),
            open_mint: false,
            recipient_output: Some(3), // Change recipient index
            current_height: Some(900_000), // Required for mainnet protocol guard
        };
        let script = build_runestone_script(&req).expect("Etch script build failed");
        
        let tx = Transaction {
            version: bitcoin::transaction::Version::TWO,
            lock_time: bitcoin::locktime::absolute::LockTime::ZERO,
            input: vec![TxIn::default()],
            output: vec![
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: script }, // [0]
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: bitcoin::ScriptBuf::new() }, // [1]
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: bitcoin::ScriptBuf::new() }, // [2]
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: bitcoin::ScriptBuf::new() }, // [3]
            ],
        };

        let artifact = Runestone::decipher(&tx);
        if let Some(Artifact::Runestone(rs)) = artifact {
            assert_eq!(rs.pointer.map(u32::from), Some(3), "Etch MUST have a pointer to recipient_output (3)");
        } else {
            panic!("Expected Runestone artifact, got {:?}", artifact);
        }
    }
}

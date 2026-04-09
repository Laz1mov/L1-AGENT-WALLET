/// gen_live_psbt.rs — PSBT Construction Binary (Runes-Aware)
///
/// Constructs unsigned PSBTs from a JSON description and prints them as
/// Base64 to stdout. The caller (Python Gateway) feeds this to the Enclave
/// Signer for signing, then broadcasts the result.
///
/// Runes integration: if the JSON request carries a `rune_mint` field,
/// two additional outputs are appended AFTER normal payment/change outputs:
///   (N)   546-sat dust → wallet's own address (Rune recipient / pointer)
///   (N+1) 0-sat OP_RETURN OP_13 <Runestone> (built by runes_forge)
///
/// The Simplicity allowance.simf contract is NOT modified:
///   - The 546-sat dust output counts as a normal spend (deducted from allowance).
///   - The 0-sat Runestone output is excluded from allowance accounting because
///     the contract already ignores zero-value outputs.

use base64::{engine::general_purpose, Engine as _};
use bitcoin::{
    script::PushBytesBuf, Address, Amount, Network, OutPoint, Psbt,
    ScriptBuf, Transaction, TxIn, TxOut, Txid,
};
use ordinals::{Edict, Etching, Runestone, SpacedRune, RuneId, Terms};
use serde::Deserialize;
use std::env;
use std::str::FromStr;

const MAX_OUTPUTS: usize = 10;
const MAX_OP_RETURN_RELAY: usize = 80;

// ── Network resolution (mirrors signer.rs — inlined for binary crate) ─────────

fn network_from_env() -> Network {
    let raw_val = env::var("BITCOIN_NETWORK")
        .unwrap_or_default()
        .to_lowercase();
    
    // 🛡️ SANITIZATION: Remove quotes/apostrophes added by some .env exporters
    let val = raw_val.trim_matches(|c| c == '\'' || c == '"');

    match val {
        "mainnet" => Network::Bitcoin,
        "testnet" => Network::Testnet,
        "regtest" => Network::Regtest,
        _         => Network::Signet, 
    }
}

// ── Deserialisation structs ───────────────────────────────────────────────────

#[derive(Deserialize)]
struct UtxoContext {
    txid: String,
    vout: u32,
    amount_sats: u64,
    script_pubkey_hex: String,
}

#[derive(Deserialize)]
struct PaymentOutput {
    address: String,
    amount_sats: u64,
}

/// Optional Rune minting parameters. Present only when the LLM calls
/// `mint_rune`. The forge builds the Runestone from these fields.
#[derive(Deserialize)]
struct RuneMintParams {
    /// Canonical Rune name (Required for Etching).
    rune_name: Option<String>,
    /// Rune ID in 'block:tx' format (Required for Minting).
    mint_id: Option<String>,
    /// Total supply (premine for Etch, or amount per Mint).
    amount: u128,
    /// Decimal places (Etch only).
    #[serde(default)]
    divisibility: u8,
    /// Ticker symbol (Etch only).
    symbol: Option<char>,
    /// If true, etch with open minting terms.
    #[serde(default)]
    open_mint: bool,
    /// Output index that receives the Runes (default=1: Runestone at [0], dust at [1]).
    /// Etch mode: becomes the `pointer` field. Mint mode: builds an Edict.
    #[serde(default)]
    recipient_output: Option<u32>,
}

#[derive(Deserialize)]
struct PsbtRequest {
    inputs: Vec<UtxoContext>,
    outputs: Vec<PaymentOutput>,
    op_return_message: Option<String>,
    fee_rate_sat_vb: f64,
    /// When present, the PSBT gains two extra outputs:
    ///   • a 546-sat dust output to the enclave address (Rune pointer)
    ///   • a 0-sat Runestone OP_RETURN output
    rune_mint: Option<RuneMintParams>,
}

// ── Runestone builder (inline, mirrors runes_forge.rs) ───────────────────────
//
// Rationale for inlining: gen_live_psbt.rs is a binary crate in src/bin/.
// To avoid a full lib.rs refactor in this phase, we replicate the core
// Runestone construction here. The canonical implementation with unit tests
// lives in src/runes_forge.rs; this is the integration layer.

fn build_runestone_txout(params: &RuneMintParams) -> Result<TxOut, String> {
    let mut runestone = Runestone {
        etching: None,
        pointer: None,
        edicts: vec![],
        mint: None,
    };

    // Recipient output index: where the Runes land.
    // Default=1 because Runestone occupies output[0] (OP_RETURN first),
    // and the 546-sat dust recipient is at output[1].
    let recipient_idx = params.recipient_output.unwrap_or(1);

    // ── 1. MINT MODE ──
    if let Some(mint_id_str) = &params.mint_id {
        let rune_id = RuneId::from_str(mint_id_str)
            .map_err(|e| format!("[FORGE] Invalid Rune ID: {}", e))?;
        runestone.mint = Some(rune_id);

        // Edict: send ALL minted runes to the recipient output.
        // amount=0 means "all unallocated" per the official Runes spec.
        // This enables mint-to: any output index can receive the minted Runes.
        runestone.edicts = vec![Edict {
            id:     rune_id,
            amount: 0,             // 0 = send all unallocated runes
            output: recipient_idx,
        }];
        eprintln!(
            "⚙️  [FORGE] Mint mode: Edict → ALL minted runes to output[{}]",
            recipient_idx
        );
    }
    // ── 2. ETCH MODE ──
    else if let Some(rune_name) = &params.rune_name {
        if params.divisibility > 38 {
            return Err(format!("[FORGE] divisibility {} exceeds 38", params.divisibility));
        }
        let spaced_rune = SpacedRune::from_str(rune_name)
            .map_err(|e| format!("[FORGE] Invalid name '{}': {}", rune_name, e))?;

        let mut etching = Etching {
            rune: Some(spaced_rune.rune),
            spacers: Some(spaced_rune.spacers),
            divisibility: Some(params.divisibility),
            symbol: params.symbol,
            premine: Some(params.amount),
            terms: None,
            turbo: false,
        };

        if params.open_mint {
            etching.terms = Some(Terms {
                amount: Some(params.amount),
                cap: Some(u128::MAX),
                height: (None, None),
                offset: (None, None),
            });
        }

        // Pointer: direct the premine to the recipient output.
        // Default=1 because Runestone sits at output[0] (OP_RETURN first).
        runestone.pointer = Some(recipient_idx);
        runestone.etching = Some(etching);
        eprintln!(
            "⚙️  [FORGE] Etch mode: pointer={} (premine → output[{}])",
            recipient_idx, recipient_idx
        );
    } else {
        return Err("[FORGE] Missing name or id".to_string());
    }

    let script = runestone.encipher();

    // Sanity-check the Runes consensus prefix before embedding in the PSBT.
    let bytes = script.as_bytes();
    if bytes.len() < 2 || bytes[0] != 0x6a || bytes[1] != 0x5d {
        return Err(format!(
            "[FORGE] Runestone prefix mismatch — expected 0x6a 0x5d, got {:#04x} {:#04x}. \
             Check ordinals crate version compatibility.",
            bytes.get(0).copied().unwrap_or(0),
            bytes.get(1).copied().unwrap_or(0)
        ));
    }

    eprintln!(
        "⚙️  [FORGE] Runestone built: {} bytes, prefix {:02x}{:02x} ✓",
        bytes.len(),
        bytes[0],
        bytes[1]
    );

    Ok(TxOut {
        value: Amount::ZERO,
        script_pubkey: script,
    })
}

// ── main ──────────────────────────────────────────────────────────────────────

fn main() {
    let args: Vec<String> = env::args().collect();

    let b64_input = match args.get(1) {
        Some(s) => s,
        None => {
            eprintln!("Usage: gen_live_psbt <base64_json_request>");
            std::process::exit(1);
        }
    };

    let json_bytes = match general_purpose::STANDARD.decode(b64_input) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("Error: Invalid Base64 input: {}", e);
            std::process::exit(1);
        }
    };

    let request: PsbtRequest = match serde_json::from_slice(&json_bytes) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("Error: Invalid JSON structure: {}", e);
            std::process::exit(1);
        }
    };

    // ── Validate request ──────────────────────────────────────────────────────
    if request.outputs.len() > MAX_OUTPUTS {
        eprintln!("Error: Too many outputs (max {})", MAX_OUTPUTS);
        std::process::exit(1);
    }
    if request.inputs.is_empty() {
        eprintln!("Error: No inputs provided");
        std::process::exit(1);
    }

    // ── Resolve input UTXO ────────────────────────────────────────────────────
    let utxo = &request.inputs[0];
    let input_txid = match Txid::from_str(&utxo.txid) {
        Ok(t) => t,
        Err(_) => {
            eprintln!("Error: Invalid input TXID");
            std::process::exit(1);
        }
    };
    let input_outpoint = OutPoint {
        txid: input_txid,
        vout: utxo.vout,
    };
    let input_spk = ScriptBuf::from_hex(&utxo.script_pubkey_hex)
        .unwrap_or_else(|_| ScriptBuf::new());
    let witness_utxo = TxOut {
        value: Amount::from_sat(utxo.amount_sats),
        script_pubkey: input_spk.clone(),
    };

    // ── Build payment outputs ─────────────────────────────────────────────────
    let mut total_spend: u64 = 0;
    let mut tx_outputs: Vec<TxOut> = Vec::new();

    for payment in &request.outputs {
        total_spend += payment.amount_sats;
        let dest_addr = match Address::from_str(payment.address.trim())
            .and_then(|a| a.require_network(network_from_env()))
        {
            Ok(a) => a,
            Err(e) => {
                eprintln!(
                    "Error: Invalid Destination Address {}: {}",
                    payment.address, e
                );
                std::process::exit(1);
            }
        };
        eprintln!(
            "⚙️  [BUILD] Payment output: {} sats → {}",
            payment.amount_sats, payment.address
        );
        tx_outputs.push(TxOut {
            value: Amount::from_sat(payment.amount_sats),
            script_pubkey: dest_addr.script_pubkey(),
        });
    }

    // ── Rune transaction layout (when rune_mint is present) ──────────────────
    //
    // CANONICAL OUTPUT ORDER (Runes protocol — OP_RETURN first):
    //
    //   [0]     0-sat OP_RETURN OP_13 <Runestone>  ← FIRST (pointer / edict → [1])
    //   [1]     546-sat dust → Rune recipient       ← default recipient_output=1
    //   [2..N]  Optional extra payment outputs
    //   [last]  Change → enclave address
    //
    // Why OP_RETURN first:
    //   • The pointer field in the Runestone can explicitly name output[1] as the
    //     Rune recipient — no ambiguity even though OP_RETURN precedes it.
    //   • For Mint mode, an Edict {id, amount:0, output:1} routes ALL minted
    //     Runes to output[1], enabling "mint-to" any address (launchpad pattern).
    //   • Explorers (mempool.space) display it cleanly with OP_RETURN on top.
    //
    // The Python Gateway declares the dust at outputs[0], but here in the binary
    // we prepend the Runestone before all payment outputs → dust lands at [1].

    let rune_params_opt = request.rune_mint.as_ref();

    // ── Pre-build Runestone TxOut ────────────────────────────────────────────
    let mut runestone_txout_opt: Option<TxOut> = None;
    if let Some(rune_params) = rune_params_opt {
        match build_runestone_txout(rune_params) {
            Ok(txout) => runestone_txout_opt = Some(txout),
            Err(e) => {
                eprintln!("Error: Runestone build failed: {}", e);
                std::process::exit(1);
            }
        }
    }

    // ── Assemble outputs: Runestone FIRST, then payments, then change ────────
    let mut ordered_outputs: Vec<TxOut> = Vec::new();

    // ❶ Runestone at index 0 (OP_RETURN) — pointer/edict already set to index 1
    if let Some(rs) = runestone_txout_opt {
        eprintln!("⚙️  [FORGE] Runestone (OP_RETURN) placed at index 0. Rune recipient: output[1].");
        ordered_outputs.push(rs);
    }

    // ❷ Payment outputs declared by Python Gateway (dust at [1] after Runestone)
    ordered_outputs.extend(tx_outputs);

    // ── Fee calculation ───────────────────────────────────────────────────────
    let num_outputs = ordered_outputs.len()
        + 1  // change
        + request.op_return_message.as_ref().map_or(0, |m| if m.is_empty() { 0 } else { 1 });
    let op_return_overhead = request.op_return_message.as_ref().map_or(0, |m| m.len());
    let estimated_vsize = 10.5
        + 58.0                                       // P2TR input
        + (43.0 * num_outputs as f64)                // payment + change + runestone outputs
        + if op_return_overhead > 0 { 40.0 } else { 0.0 };
    let fee = (estimated_vsize * request.fee_rate_sat_vb).ceil() as u64;

    // ── Change output (appended last for Rune txs) ────────────────────────────
    let change_sats = utxo
        .amount_sats
        .saturating_sub(total_spend)
        .saturating_sub(fee);

    if change_sats >= 330 {
        eprintln!("⚙️  [BUILD] Change output: {} sats → enclave", change_sats);
        ordered_outputs.push(TxOut {
            value: Amount::from_sat(change_sats),
            script_pubkey: input_spk,
        });
    }

    // ── Legacy OP_RETURN (NOT Runes) — standard tx, appended last ─────────────
    if let Some(msg) = &request.op_return_message {
        if !msg.is_empty() && rune_params_opt.is_none() {
            let op_return_data = PushBytesBuf::try_from(msg.as_bytes().to_vec()).unwrap();
            ordered_outputs.push(TxOut {
                value: Amount::ZERO,
                script_pubkey: ScriptBuf::new_op_return(&op_return_data),
            });
        }
    }

    // ── Assemble and serialise PSBT ───────────────────────────────────────────
    let tx = Transaction {
        version: bitcoin::transaction::Version::TWO,
        lock_time: bitcoin::locktime::absolute::LockTime::ZERO,
        input: vec![TxIn {
            previous_output: input_outpoint,
            script_sig: ScriptBuf::new(),
            sequence: bitcoin::Sequence::MAX,
            witness: bitcoin::Witness::new(),
        }],
        output: ordered_outputs,
    };

    match Psbt::from_unsigned_tx(tx) {
        Ok(mut psbt) => {
            psbt.inputs[0].witness_utxo = Some(witness_utxo);
            println!("{}", general_purpose::STANDARD.encode(psbt.serialize()));
        }
        Err(e) => {
            eprintln!("Error: Failed to create PSBT: {}", e);
            std::process::exit(1);
        }
    }
}

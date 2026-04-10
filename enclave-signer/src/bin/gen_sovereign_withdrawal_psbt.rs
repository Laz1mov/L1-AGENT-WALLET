/// gen_sovereign_withdrawal_psbt.rs
///
/// Generates a PSBT for spending from the Sovereign Agent's Taproot address
/// via the RECOVERY SCRIPT PATH (Leaf 0 in Legacy mode).
///
/// The PSBT includes full BIP-371 Taproot metadata:
///   - tap_internal_key
///   - tap_scripts (the recovery leaf script + control block)
///   - witness_utxo
///
/// This allows an external signer (Sparrow, Bitcoin Core, etc.) to sign
/// using the human master private key without needing the enclave.
///
/// Usage:
///   gen_sovereign_withdrawal_psbt <base64_json>
///
/// JSON payload:
/// {
///   "destination": "bc1p...",
///   "fee_rate_sat_vb": 2.0,
///   "utxos": [{"txid": "...", "vout": 0, "value": 10000}]
/// }

use bitcoin::{
    Address, Amount, OutPoint, Psbt, ScriptBuf, Sequence, Transaction, TxIn, TxOut, Witness,
    XOnlyPublicKey,
};
use bitcoin::absolute::LockTime;
use bitcoin::taproot::{LeafVersion, TaprootBuilder};
use bitcoin::transaction::Version;
use enclave_signer::signer;
use enclave_signer::hd_wallet;
use secp256k1::{Keypair, Secp256k1};
use serde::Deserialize;
use std::env;
use std::str::FromStr;

#[derive(Deserialize)]
struct WithdrawalRequest {
    destination: String,
    fee_rate_sat_vb: f64,
    utxos: Vec<UtxoInput>,
}

#[derive(Deserialize)]
struct UtxoInput {
    txid: String,
    vout: u32,
    value: u64,
}

fn network_from_env() -> bitcoin::Network {
    let raw_val = env::var("BITCOIN_NETWORK").unwrap_or_default().to_lowercase();
    let val = raw_val.trim_matches(|c| c == '\'' || c == '"');
    match val {
        "mainnet" => bitcoin::Network::Bitcoin,
        "testnet" => bitcoin::Network::Testnet,
        "regtest" => bitcoin::Network::Regtest,
        _ => bitcoin::Network::Signet,
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: gen_sovereign_withdrawal_psbt <base64_json>");
        std::process::exit(1);
    }

    // 1. Decode request
    let json_bytes = base64::Engine::decode(
        &base64::engine::general_purpose::STANDARD,
        &args[1],
    )
    .expect("Invalid base64");
    let req: WithdrawalRequest =
        serde_json::from_slice(&json_bytes).expect("Invalid JSON payload");

    // 2. Load enclave identity (to reconstruct the Taproot tree)
    let seed_hex = env::var("ENCLAVE_SEED")
        .or_else(|_| env::var("ENCLAVE_SECRET_KEY"))
        .expect("ENCLAVE_SEED or ENCLAVE_SECRET_KEY not set");
    
    let index = env::var("ENCLAVE_DERIVATION_INDEX")
        .unwrap_or_else(|_| "0".to_string())
        .parse::<u32>()
        .unwrap_or(0);

    let network = signer::network_from_env();
    let seed = hex::decode(seed_hex.trim_matches(|c| c == '\'' || c == '"')).expect("Invalid seed hex");
    
    let secp = Secp256k1::new();
    let sk = hd_wallet::derive_enclave_key(&seed, index, network).expect("Failed to derive enclave key");
    let keypair = Keypair::from_secret_key(&secp, &sk);
    let (internal_key, _) = keypair.x_only_public_key();

    // 3. Build the same MAST tree as the enclave
    let master_pubkey_hex = env::var("MASTER_HUMAN_PUBKEY")
        .unwrap_or_else(|_| "03b0a480da7a611dce56c10fe3bedb27cb21c452a8974bc949c4b7fcd8feb60316".to_string());
    
    // Strip parity prefix if present
    let cleaned_pubkey = if master_pubkey_hex.len() == 66
        && (master_pubkey_hex.starts_with("02") || master_pubkey_hex.starts_with("03"))
    {
        &master_pubkey_hex[2..]
    } else {
        &master_pubkey_hex
    };
    let master_xonly = XOnlyPublicKey::from_str(cleaned_pubkey).expect("Invalid master pubkey");

    // Recovery script: <master_xonly_pubkey> OP_CHECKSIG
    let recovery_script = ScriptBuf::builder()
        .push_slice(master_xonly.serialize())
        .push_opcode(bitcoin::opcodes::all::OP_CHECKSIG)
        .into_script();

    let legacy_mode = env::var("ENCLAVE_LEGACY_MODE").unwrap_or_default() == "true";

    let spend_info = if legacy_mode {
        TaprootBuilder::new()
            .add_leaf(0, recovery_script.clone())
            .expect("Failed to add recovery leaf")
            .finalize(&secp, internal_key)
            .expect("Failed to finalize taproot")
    } else {
        // Hardened mode — recovery is at depth 1
        let allowance_script = ScriptBuf::builder()
            .push_slice(b"simplicity_allowance_v1")
            .into_script();
        let governance_script = ScriptBuf::builder()
            .push_slice(b"simplicity_governance_v1")
            .into_script();

        TaprootBuilder::new()
            .add_leaf(1, recovery_script.clone())
            .expect("Failed to add recovery leaf")
            .add_leaf(2, allowance_script)
            .expect("Failed to add allowance leaf")
            .add_leaf(2, governance_script)
            .expect("Failed to add governance leaf")
            .finalize(&secp, internal_key)
            .expect("Failed to finalize taproot")
    };

    let network = network_from_env();
    let agent_address = Address::p2tr(
        &secp,
        internal_key,
        spend_info.merkle_root(),
        network,
    );
    let agent_spk = agent_address.script_pubkey();

    eprintln!("🏛️  Agent Address: {}", agent_address);
    eprintln!("🔑 Internal Key:  {}", internal_key);
    eprintln!("🌿 Recovery Script: {}", recovery_script);

    // 4. Parse destination
    let dest_addr = Address::from_str(&req.destination)
        .expect("Invalid destination address")
        .assume_checked();

    // 5. Build transaction inputs
    let mut tx = Transaction {
        version: Version::TWO,
        lock_time: LockTime::ZERO,
        input: Vec::new(),
        output: Vec::new(),
    };

    let mut total_input = 0u64;
    let mut witness_utxos = Vec::new();

    for utxo in &req.utxos {
        let txid = bitcoin::Txid::from_str(&utxo.txid).expect("Invalid txid");
        tx.input.push(TxIn {
            previous_output: OutPoint { txid, vout: utxo.vout },
            script_sig: ScriptBuf::new(),
            sequence: Sequence::MAX,
            witness: Witness::new(),
        });
        total_input += utxo.value;
        witness_utxos.push(TxOut {
            value: Amount::from_sat(utxo.value),
            script_pubkey: agent_spk.clone(),
        });
    }

    // 6. Estimate fee for script path spend
    // Script path witness: control_block (~65 bytes) + script (~34 bytes) + signature (64 bytes)
    // ~163 witness bytes per input + ~43 bytes output overhead
    let estimated_vbytes = (10 + 41 * tx.input.len() + 43 + (163 * tx.input.len() + 3) / 4) as u64;
    let fee_sats = (estimated_vbytes as f64 * req.fee_rate_sat_vb).ceil() as u64;

    if total_input <= fee_sats + 546 {
        eprintln!("❌ Insufficient funds: {} sats available, need {} + 546 dust",
            total_input, fee_sats);
        std::process::exit(1);
    }

    let send_amount = total_input - fee_sats;

    tx.output.push(TxOut {
        value: Amount::from_sat(send_amount),
        script_pubkey: dest_addr.script_pubkey(),
    });

    eprintln!("💰 Total Input:  {} sats", total_input);
    eprintln!("⛏️  Fee:          {} sats ({} vB × {:.1} sat/vB)",
        fee_sats, estimated_vbytes, req.fee_rate_sat_vb);
    eprintln!("📤 Send Amount:  {} sats", send_amount);
    eprintln!("📍 Destination:  {}", req.destination);

    // 7. Build PSBT with full BIP-371 Taproot metadata
    let mut psbt = Psbt::from_unsigned_tx(tx).expect("Failed to create PSBT");

    // Get the control block for the recovery script leaf
    let control_block = spend_info
        .control_block(&(recovery_script.clone(), LeafVersion::TapScript))
        .expect("Failed to compute control block for recovery leaf");

    for i in 0..psbt.inputs.len() {
        // witness_utxo (required for Taproot)
        psbt.inputs[i].witness_utxo = Some(witness_utxos[i].clone());

        // tap_internal_key
        psbt.inputs[i].tap_internal_key = Some(internal_key);

        // tap_scripts: map from (control_block) -> (script, leaf_version)
        psbt.inputs[i].tap_scripts.insert(
            control_block.clone(),
            (recovery_script.clone(), LeafVersion::TapScript),
        );

        // tap_key_origins for the master key (so Sparrow can locate the signing key)
        // BIP-371: map XOnlyPublicKey -> (Vec<TapLeafHash>, (Fingerprint, DerivationPath))
        let tap_leaf_hash = bitcoin::taproot::TapLeafHash::from_script(
            &recovery_script,
            LeafVersion::TapScript,
        );
        psbt.inputs[i].tap_key_origins.insert(
            master_xonly,
            (
                vec![tap_leaf_hash].into_iter().collect(),
                (bitcoin::bip32::Fingerprint::default(), bitcoin::bip32::DerivationPath::default()),
            ),
        );
    }

    // 8. Serialize and output as Base64
    use base64::Engine;
    let psbt_b64 = base64::engine::general_purpose::STANDARD.encode(psbt.serialize());
    println!("{}", psbt_b64);

    eprintln!("\n✅ PSBT generated successfully.");
    eprintln!("📋 Import this Base64 PSBT into Sparrow Wallet to sign with your master key.");
}

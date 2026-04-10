/// gen_legacy_recovery_psbt.rs
/// 
/// Standalone Emergency Recovery Tool for Legacy Addresses (Pre-Caméléon).
/// This binary bypasses BIP-86 HD derivation and uses the STATIC secret key 
/// and Legacy MAST structure (Depth 0) to recover funds from old addresses.
///
/// Usage: 
///   export ENCLAVE_SECRET_KEY="<static_key>"
///   gen_legacy_recovery_psbt <base64_json>

use bitcoin::{
    Address, Amount, OutPoint, Psbt, ScriptBuf, Sequence, Transaction, TxIn, TxOut, Witness,
    XOnlyPublicKey, Network,
};
use bitcoin::absolute::LockTime;
use bitcoin::taproot::{LeafVersion, TaprootBuilder};
use bitcoin::transaction::Version;
use secp256k1::{Keypair, Secp256k1, SecretKey};
use serde::Deserialize;
use base64::Engine;
use std::env;
use std::str::FromStr;

#[derive(Deserialize)]
struct RecoveryRequest {
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

fn network_from_env() -> Network {
    let raw_val = env::var("BITCOIN_NETWORK").unwrap_or_default().to_lowercase();
    match raw_val.trim_matches(|c| c == '\'' || c == '"') {
        "mainnet" => Network::Bitcoin,
        "testnet" => Network::Testnet,
        "regtest" => Network::Regtest,
        _ => Network::Signet,
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: gen_legacy_recovery_psbt <base64_json>");
        std::process::exit(1);
    }

    let json_bytes = base64::Engine::decode(
        &base64::engine::general_purpose::STANDARD,
        &args[1],
    ).expect("Invalid base64");
    
    let req: RecoveryRequest = serde_json::from_slice(&json_bytes).expect("Invalid JSON payload");

    // 1. Static Key from ENV
    let secret_hex = env::var("ENCLAVE_SECRET_KEY")
        .or_else(|_| env::var("ENCLAVE_SEED"))
        .expect("ENCLAVE_SECRET_KEY not set");
    
    let secp = Secp256k1::new();
    let sk = SecretKey::from_str(secret_hex.trim_matches(|c| c == '\'' || c == '"')).expect("Invalid secret key");
    let keypair = Keypair::from_secret_key(&secp, &sk);
    let (internal_key, _parity) = keypair.x_only_public_key();

    // 2. Master Human Key
    let master_pubkey_hex = env::var("MASTER_HUMAN_PUBKEY")
        .unwrap_or_else(|_| "03b0a480da7a611dce56c10fe3bedb27cb21c452a8974bc949c4b7fcd8feb60316".to_string());
    
    let cleaned_pubkey = if master_pubkey_hex.len() == 66 { &master_pubkey_hex[2..] } else { &master_pubkey_hex };
    let master_xonly = XOnlyPublicKey::from_str(cleaned_pubkey).expect("Invalid master pubkey");

    let recovery_script = ScriptBuf::builder()
        .push_slice(master_xonly.serialize())
        .push_opcode(bitcoin::opcodes::all::OP_CHECKSIG)
        .into_script();

    let network = network_from_env();
    
    // ─── EXHAUSTIVE SCAN MODE ───
    println!("\n🔍 SOVEREIGN FORENSIC ULTIMATE SCAN");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("🔑 Enclave Key: {}", internal_key);
    println!("👤 Master Key:  {}", master_xonly);
    println!("🌐 Network:     {:?}", network);
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

    let mut candidates = Vec::new();

    // Group 1: Enclave as Internal Key
    let p = internal_key;
    let m = master_xonly;
    
    // A: BIP-86
    candidates.push(("Enclave BIP-86", Address::p2tr(&secp, p, None, network), None, None));

    // B: Legacy Depth 0
    let spend_info = TaprootBuilder::new().add_leaf(0, recovery_script.clone()).unwrap().finalize(&secp, p).unwrap();
    candidates.push(("Enclave Legacy Depth 0", Address::p2tr(&secp, p, spend_info.merkle_root(), network), Some(spend_info), Some(recovery_script.clone())));

    // C: Hardened Permutations (1, 2, 2)
    let leaves = vec![
        ("Recovery", recovery_script.clone()),
        ("Whale", ScriptBuf::builder().push_slice(p.serialize()).push_slice(m.serialize()).push_opcode(bitcoin::opcodes::all::OP_CHECKMULTISIG).into_script()),
        ("Allowance", ScriptBuf::new()),
    ];

    // Permutation 1: Allowance (1), Whale (2), Recovery (2)
    let info = TaprootBuilder::new().add_leaf(1, leaves[2].1.clone()).unwrap().add_leaf(2, leaves[1].1.clone()).unwrap().add_leaf(2, leaves[0].1.clone()).unwrap().finalize(&secp, p).unwrap();
    candidates.push(("Hardened (Allow1, Whale2, Recov2)", Address::p2tr(&secp, p, info.merkle_root(), network), Some(info), Some(recovery_script.clone())));

    // Permutation 2: Recovery (1), Whale (2), Allowance (2)
    let info = TaprootBuilder::new().add_leaf(1, leaves[0].1.clone()).unwrap().add_leaf(2, leaves[1].1.clone()).unwrap().add_leaf(2, leaves[2].1.clone()).unwrap().finalize(&secp, p).unwrap();
    candidates.push(("Hardened (Recov1, Whale2, Allow2)", Address::p2tr(&secp, p, info.merkle_root(), network), Some(info), Some(recovery_script.clone())));

    // Group 2: Master as Internal Key (Inverted)
    let p = master_xonly;
    let enclave_recovery = ScriptBuf::builder().push_slice(internal_key.serialize()).push_opcode(bitcoin::opcodes::all::OP_CHECKSIG).into_script();
    
    candidates.push(("Master BIP-86 (Inverted)", Address::p2tr(&secp, p, None, network), None, None));
    
    let info = TaprootBuilder::new().add_leaf(0, enclave_recovery.clone()).unwrap().finalize(&secp, p).unwrap();
    candidates.push(("Master Legacy Depth 0 (Inverted)", Address::p2tr(&secp, p, info.merkle_root(), network), Some(info), Some(enclave_recovery.clone())));

    // Candidate discovery
    for (name, addr, _, _) in &candidates {
        println!("{:<35} : {}", name, addr);
    }

    println!("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("Searching for: bc1pzjfr...");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

    // Match and generate PSBT if target provided in dest
    let mut selected_idx = 1; // Default to Enclave Legacy
    for (i, (_, addr, _, _)) in candidates.iter().enumerate() {
       if addr.to_string().starts_with("bc1pzjfr") {
           println!("🎯 MATCH FOUND: index {}", i);
           selected_idx = i;
       }
    }

    let (_, agent_address, spend_info_opt, script_opt) = &candidates[selected_idx];
    let spend_info = spend_info_opt.as_ref().expect("Cannot spend BIP-86 via script path tool");
    let recovery_script = script_opt.as_ref().unwrap();
    
    // Build transaction
    let dest_addr = Address::from_str(&req.destination).expect("Invalid dest").assume_checked();
    let mut tx = Transaction {
        version: Version::TWO,
        lock_time: LockTime::ZERO,
        input: Vec::new(),
        output: Vec::new(),
    };

    let mut total_input = 0u64;
    let mut witness_utxos = Vec::new();
    let agent_spk = agent_address.script_pubkey();

    for utxo in &req.utxos {
        tx.input.push(TxIn {
            previous_output: OutPoint { txid: bitcoin::Txid::from_str(&utxo.txid).unwrap(), vout: utxo.vout },
            script_sig: ScriptBuf::new(),
            sequence: Sequence::MAX,
            witness: Witness::new(),
        });
        total_input += utxo.value;
        witness_utxos.push(TxOut { value: Amount::from_sat(utxo.value), script_pubkey: agent_spk.clone() });
    }

    let estimated_vbytes = (10 + 41 * tx.input.len() + 43 + (163 * tx.input.len() + 3) / 4) as u64;
    let fee_sats = (estimated_vbytes as f64 * req.fee_rate_sat_vb).ceil() as u64;
    let send_amount = total_input - fee_sats;

    tx.output.push(TxOut { value: Amount::from_sat(send_amount), script_pubkey: dest_addr.script_pubkey() });

    let mut psbt = Psbt::from_unsigned_tx(tx).unwrap();
    let control_block = spend_info.control_block(&(recovery_script.clone(), LeafVersion::TapScript)).unwrap();

    for i in 0..psbt.inputs.len() {
        psbt.inputs[i].witness_utxo = Some(witness_utxos[i].clone());
        psbt.inputs[i].tap_internal_key = Some(internal_key);
        psbt.inputs[i].tap_scripts.insert(control_block.clone(), (recovery_script.clone(), LeafVersion::TapScript));
        
        let tap_leaf_hash = bitcoin::taproot::TapLeafHash::from_script(&recovery_script, LeafVersion::TapScript);
        psbt.inputs[i].tap_key_origins.insert(master_xonly, (vec![tap_leaf_hash].into_iter().collect(), (bitcoin::bip32::Fingerprint::default(), bitcoin::bip32::DerivationPath::default())));
    }

    println!("{}", base64::engine::general_purpose::STANDARD.encode(psbt.serialize()));
    eprintln!("\n✅ PSBT generated. Import into Sparrow to sign with master key.");
}

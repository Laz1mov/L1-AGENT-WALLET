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
    
    // ─── FORENSIC DISCOVERY MODE ───
    println!("\n🔍 SOVEREIGN FORENSIC DISCOVERY");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("🔑 Internal Key (Enclave Static): {}", internal_key);
    println!("👤 Master Human Key:             {}", master_xonly);
    println!("🌐 Active Network:                {:?}", network);
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

    // Candidate A: BIP-86 (Key Path Only)
    let addr_bip86 = Address::p2tr(&secp, internal_key, None, network);
    println!("A [BIP-86 Key Path Only]:   {}", addr_bip86);

    // Candidate B: Legacy MAST (1 Leaf @ Depth 0)
    let spend_info_legacy = TaprootBuilder::new()
        .add_leaf(0, recovery_script.clone())
        .unwrap()
        .finalize(&secp, internal_key)
        .unwrap();
    let addr_legacy = Address::p2tr(&secp, internal_key, spend_info_legacy.merkle_root(), network);
    println!("B [Legacy MAST Depth 0]:    {}", addr_legacy);

    // Candidate C: Hardened MAST (3 Leaves)
    // Structure: Leaf 1 (Depth 1), Leaf 2 (Depth 2), Leaf 3 (Depth 2)
    // We use the Genesis default scripts to ensure the Merkle Root matches.
    let allowance_script = ScriptBuf::new(); // Default allowance was empty/fallback
    let whale_script = ScriptBuf::builder()
        .push_slice(internal_key.serialize()) // Enclave
        .push_slice(master_xonly.serialize())  // Master
        .push_opcode(bitcoin::opcodes::all::OP_CHECKMULTISIG) // Legacy 2-of-2 logic
        .into_script();

    let spend_info_hardened = TaprootBuilder::new()
        .add_leaf(1, allowance_script.clone()).unwrap() 
        .add_leaf(2, whale_script.clone()).unwrap()
        .add_leaf(2, recovery_script.clone()).unwrap()
        .finalize(&secp, internal_key)
        .unwrap();
    let addr_hardened = Address::p2tr(&secp, internal_key, spend_info_hardened.merkle_root(), network);
    println!("C [Hardened MAST 3-Leaf]:   {}", addr_hardened);

    println!("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("If your target address bc1pzj... is listed above, copy its letter.");
    println!("Currently generating PSBT for: C [HARDENED MAST 3-LEAF]");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

    // ─── Generate PSBT for Candidate C (Hardened) ───
    let agent_address = addr_hardened;
    let spend_info = spend_info_hardened;
    
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

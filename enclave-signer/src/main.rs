use enclave_signer::policy::{SovereignPolicy, SpendingPolicy};
use enclave_signer::signer::{self, EnclaveSigner, BatchManifest};
use enclave_signer::simplicity_engine::SimplicityEngine;
use base64::{engine::general_purpose, Engine as _};
use bitcoin::{Psbt, Witness, Address, Network};
use serde::{Deserialize, Serialize};
use log::{info, warn, error};
use std::path::Path;
use std::str::FromStr;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[derive(Serialize, Deserialize)]
struct SignRequest {
    #[serde(rename = "type")]
    request_type: String,
    psbt_base64: Option<String>,
    amount_sats: Option<u64>, 
    provided_signatures: Option<Vec<String>>,
    batch_manifest: Option<signer::BatchManifest>,
    mandate_signature: Option<String>,
    new_whale_policy: Option<SpendingPolicy>,
    new_recovery_policy: Option<SpendingPolicy>,
    new_allowance: Option<u64>,
    upgrade_proof: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct SignResponse {
    #[serde(rename = "type")]
    response_type: String,
    address: Option<String>,
    script_pubkey_hex: Option<String>,
    signature_hex: Option<String>,
    signed_psbt_base64: Option<String>,
    signed_batch_psbts: Option<Vec<String>>,
    txid: Option<String>,
    raw_hex: Option<String>,
    policy: Option<SovereignPolicy>,
    error: Option<String>,
}

const POLICY_FILE: &str = "policy.json";

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dotenvy::dotenv().ok();
    env_logger::init();
    let seed_hex = std::env::var("ENCLAVE_SEED")
        .or_else(|_| std::env::var("ENCLAVE_SECRET_KEY"))
        .expect("🚨 ENCLAVE_SEED or ENCLAVE_SECRET_KEY must be set");

    let index = std::env::var("ENCLAVE_DERIVATION_INDEX")
        .unwrap_or_else(|_| "0".to_string())
        .parse::<u32>()
        .unwrap_or(0);

    let network = signer::network_from_env();
    let signer = Arc::new(EnclaveSigner::new_hd(&seed_hex, index, network)?);
    info!("🛡️  [ENCLAVE] Sovereign Signer active (BIP-86 Index: {})", index);
    let listener = TcpListener::bind("0.0.0.0:7777").await?;
    info!("🛡️  [ENCLAVE] Sovereign Signer active on 0.0.0.0:7777");

    loop {
        let (mut socket, _) = listener.accept().await?;
        let signer = Arc::clone(&signer);

        tokio::spawn(async move {
            let mut buf = [0; 8192]; 
            loop {
                let n = match socket.read(&mut buf).await {
                    Ok(0) => return,
                    Ok(n) => n,
                    Err(e) => {
                        error!("[ENCLAVE] Socket read error: {}", e);
                        return;
                    }
                };

                let request: SignRequest = match serde_json::from_slice(&buf[..n]) {
                    Ok(req) => req,
                    Err(e) => {
                        send_error(&mut socket, &format!("Invalid JSON Protocol: {}", e)).await;
                        continue;
                    }
                };

                let response = match request.request_type.as_str() {
                    "GetPolicy" | "GetAddress" => {
                        let policy = SovereignPolicy::load_from_seal(Path::new(POLICY_FILE))
                            .unwrap_or_else(|_| SovereignPolicy::new_default());
                        SignResponse {
                            response_type: "Policy".to_string(),
                            address: Some(signer.get_taproot_address(&policy)),
                            script_pubkey_hex: Some(hex::encode(signer.get_script_pubkey(&policy))),
                            signature_hex: None,
                            signed_psbt_base64: None,
                            signed_batch_psbts: None,
                            txid: None,
                            raw_hex: None,
                            policy: Some(policy),
                            error: None,
                        }
                    },
                    "SignTransaction" => handle_sign_transaction(&signer, request).await,
                    "SignBatchChain" => handle_sign_batch_chain(&signer, request).await,
                    "SignLegacySweep" => handle_sign_legacy_sweep(&signer, request).await,
                    "UpdatePolicy" => handle_update_policy(&signer, request).await,
                    _ => error_response("Unknown request type"),
                };

                let response_bytes = match serde_json::to_vec(&response) {
                    Ok(bytes) => bytes,
                    Err(e) => {
                        error!("[ENCLAVE] Serialization error: {}", e);
                        return;
                    }
                };
                let _ = socket.write_all(&response_bytes).await;
            }
        });
    }
}

async fn handle_sign_transaction(signer: &EnclaveSigner, request: SignRequest) -> SignResponse {
    let policy = SovereignPolicy::load_from_seal(Path::new(POLICY_FILE))
        .unwrap_or_else(|_| SovereignPolicy::new_default());

    let psbt_b64 = match request.psbt_base64 {
        Some(p) => p,
        None => return error_response("Missing psbt_base64"),
    };

    let psbt_bytes = match general_purpose::STANDARD.decode(&psbt_b64) {
        Ok(b) => b,
        Err(e) => return error_response(&format!("Base64 Decode Error: {}", e)),
    };

    let mut psbt = match Psbt::deserialize(&psbt_bytes) {
        Ok(p) => p,
        Err(e) => return error_response(&format!("Invalid PSBT: {}", e)),
    };

    if !psbt.inputs.is_empty() {
        if psbt.inputs[0].witness_utxo.is_none() {
            let utxo = bitcoin::TxOut {
                value: bitcoin::Amount::from_sat(request.amount_sats.unwrap_or(10000)),
                script_pubkey: signer.get_script_pubkey(&policy),
            };
            psbt.inputs[0].witness_utxo = Some(utxo);
        }
    }

    if let Err(e) = SimplicityEngine::execute_allowance_contract(&psbt, policy.allowance_sats) {
        let err_msg: String = e.to_string();
        return error_response(&err_msg);
    }

    let tx = &psbt.unsigned_tx;
    let prevouts = vec![psbt.inputs[0].witness_utxo.clone().unwrap()];
    let mut cache = bitcoin::sighash::SighashCache::new(tx);
    let spend_info = signer.get_taproot_info(&policy);
    
    let sighash = match cache.taproot_signature_hash(0, &bitcoin::sighash::Prevouts::All(&prevouts), None, None, bitcoin::sighash::TapSighashType::All) {
        Ok(h) => h,
        Err(e) => return error_response(&format!("Sighash Error: {}", e)),
    };

    let mut msg_hash = [0u8; 32];
    msg_hash.copy_from_slice(sighash.as_ref());
    let raw_sig = signer.sign_schnorr(msg_hash, spend_info.merkle_root());

    // 🛡️ FINALIZE: Embed signature and extract raw hex
    let sig = bitcoin::taproot::Signature {
        signature: raw_sig,
        sighash_type: bitcoin::sighash::TapSighashType::All,
    };
    psbt.inputs[0].tap_key_sig = Some(sig);

    let mut witness = Witness::new();
    let mut sig_wit = raw_sig.as_ref().to_vec();
    sig_wit.push(0x01); // SIGHASH_ALL
    witness.push(sig_wit);
    psbt.inputs[0].final_script_witness = Some(witness);

    match psbt.clone().extract_tx() {
        Ok(tx) => SignResponse {
            response_type: "Signature".to_string(),
            address: Some(signer.get_taproot_address(&policy)),
            script_pubkey_hex: Some(hex::encode(signer.get_script_pubkey(&policy))),
            signature_hex: Some(hex::encode(raw_sig.as_ref())),
            signed_psbt_base64: Some(general_purpose::STANDARD.encode(psbt.serialize())),
            signed_batch_psbts: None,
            txid: Some(tx.compute_txid().to_string()),
            raw_hex: Some(hex::encode(bitcoin::consensus::serialize(&tx))),
            policy: Some(policy),
            error: None,
        },
        Err(e) => error_response(&format!("Tx Extraction Error: {}", e)),
    }
}

async fn handle_sign_legacy_sweep(signer: &EnclaveSigner, request: SignRequest) -> SignResponse {
    let psbt_b64 = match request.psbt_base64 {
        Some(p) => p,
        None => return error_response("Missing psbt_base64"),
    };

    let psbt_bytes = match general_purpose::STANDARD.decode(&psbt_b64) {
        Ok(b) => b,
        Err(e) => return error_response(&format!("Base64 Decode Error: {}", e)),
    };

    let mut psbt = match Psbt::deserialize(&psbt_bytes) {
        Ok(p) => p,
        Err(e) => return error_response(&format!("Invalid PSBT: {}", e)),
    };

    let network = signer::network_from_env();
    let bip86_addr_str = signer.get_taproot_address_for_network(network);
    let bip86_spk = Address::from_str(&bip86_addr_str).unwrap().assume_checked().script_pubkey();
    
    // Also get current policy-tweaked address for comparison
    let policy_path = Path::new(POLICY_FILE);
    let policy_opt = if policy_path.exists() {
        SovereignPolicy::load_from_seal(policy_path).ok()
    } else {
        None
    };

    let mut prevouts = Vec::new();
    for input in &psbt.inputs {
        let utxo = match input.witness_utxo.as_ref() {
            Some(u) => u,
            None => return error_response("Missing witness_utxo"),
        };
        prevouts.push(utxo.clone());
    }

    for i in 0..psbt.unsigned_tx.input.len() {
        let utxo = &prevouts[i];
        let mut cache = bitcoin::sighash::SighashCache::new(&psbt.unsigned_tx);
        let sighash = match cache.taproot_signature_hash(i, &bitcoin::sighash::Prevouts::All(&prevouts), None, None, bitcoin::sighash::TapSighashType::All) {
            Ok(h) => h,
            Err(e) => return error_response(&format!("Sighash Error: {}", e)),
        };

        let mut msg_hash = [0u8; 32];
        msg_hash.copy_from_slice(sighash.as_ref());

        // 🛡️ SMART TWEAK DETECTION
        // If the SPK matches the pure BIP-86, use no tweak.
        // If it looks like a MAST tweak or something else, try current policy tweak.
        let merkle_root = if utxo.script_pubkey == bip86_spk {
            None
        } else {
            policy_opt.as_ref().map(|p| signer.get_taproot_info(p).merkle_root()).flatten()
        };

        let raw_sig = signer.sign_schnorr(msg_hash, merkle_root);
        
        let sig = bitcoin::taproot::Signature {
            signature: raw_sig,
            sighash_type: bitcoin::sighash::TapSighashType::All,
        };

        psbt.inputs[i].tap_key_sig = Some(sig);
        
        // 🛡️ FINALIZE
        let mut witness = Witness::new();
        let mut sig_wit = raw_sig.as_ref().to_vec();
        sig_wit.push(0x01); // SIGHASH_ALL
        witness.push(sig_wit);
        psbt.inputs[i].final_script_witness = Some(witness);
    }

    match psbt.extract_tx() {
        Ok(tx) => SignResponse {
            response_type: "LegacySweepResult".to_string(),
            address: Some(bip86_addr_str),
            script_pubkey_hex: None,
            signature_hex: None,
            signed_psbt_base64: None,
            signed_batch_psbts: None,
            txid: Some(tx.compute_txid().to_string()),
            raw_hex: Some(hex::encode(bitcoin::consensus::serialize(&tx))),
            policy: None,
            error: None,
        },
        Err(e) => error_response(&format!("Tx Extraction Error: {}", e)),
    }
}

async fn handle_sign_batch_chain(signer: &EnclaveSigner, request: SignRequest) -> SignResponse {
    let policy = SovereignPolicy::load_from_seal(Path::new(POLICY_FILE))
        .unwrap_or_else(|_| SovereignPolicy::new_default());

    let manifest = match request.batch_manifest {
        Some(m) => m,
        None => return error_response("Missing manifest"),
    };
    let sig_hex = match request.mandate_signature {
        Some(s) => s,
        None => return error_response("Missing signature"),
    };

    let master_pubkey = match &policy.recovery_policy {
        SpendingPolicy::SingleSig { pubkey } => pubkey.clone(),
        _ => return error_response("Invalid policy for mandates"),
    };

    if let Err(e) = signer.verify_master_mandate(&manifest, &sig_hex, &master_pubkey) {
        return error_response(&format!("Mandate Rejected: {}", e));
    }

    let psbt_b64 = request.psbt_base64.unwrap_or_default();
    let psbt_bytes = general_purpose::STANDARD.decode(psbt_b64).unwrap_or_default();
    let psbt = Psbt::deserialize(&psbt_bytes).unwrap();
    
    let initial_outpoint = psbt.unsigned_tx.input[0].previous_output;
    let initial_utxo = psbt.inputs[0].witness_utxo.clone().unwrap();

    match signer.sign_batch_chain(&policy, initial_utxo, initial_outpoint, &manifest) {
        Ok(psbts) => SignResponse {
            response_type: "BatchSignature".to_string(),
            address: Some(signer.get_taproot_address(&policy)),
            script_pubkey_hex: None,
            signature_hex: None,
            signed_psbt_base64: None,
            signed_batch_psbts: Some(psbts),
            txid: None,
            raw_hex: None,
            policy: Some(policy),
            error: None,
        },
        Err(e) => error_response(&format!("Batch Sign Error: {}", e)),
    }
}

async fn handle_update_policy(signer: &EnclaveSigner, request: SignRequest) -> SignResponse {
    let mut policy = SovereignPolicy::load_from_seal(Path::new(POLICY_FILE))
        .unwrap_or_else(|_| SovereignPolicy::new_default());

    match policy.seal_to_disk(Path::new(POLICY_FILE)) {
        Ok(_) => SignResponse {
            response_type: "Success".to_string(),
            address: Some(signer.get_taproot_address(&policy)),
            script_pubkey_hex: None,
            signature_hex: None,
            signed_psbt_base64: None,
            signed_batch_psbts: None,
            txid: None,
            raw_hex: None,
            policy: Some(policy),
            error: None,
        },
        Err(e) => error_response(&format!("Seal Error: {}", e)),
    }
}

fn error_response(msg: &str) -> SignResponse {
    SignResponse {
        response_type: "Error".to_string(),
        address: None,
        script_pubkey_hex: None,
        signature_hex: None,
        signed_psbt_base64: None,
        signed_batch_psbts: None,
        txid: None,
        raw_hex: None,
        policy: None,
        error: Some(msg.to_string()),
    }
}

async fn send_error(socket: &mut tokio::net::TcpStream, msg: &str) {
    let resp = error_response(msg);
    if let Ok(bytes) = serde_json::to_vec(&resp) {
        let _ = socket.write_all(&bytes).await;
    }
}

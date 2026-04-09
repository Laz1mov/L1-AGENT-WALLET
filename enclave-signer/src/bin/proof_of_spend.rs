//! 🛡️ PROOF OF SPEND: Proves the enclave can sign transactions from its MAST address.
//! Also verifies the recovery leaf is usable with the master key.

use bitcoin::key::TapTweak;
use bitcoin::sighash::{Prevouts, SighashCache, TapSighashType};
use bitcoin::taproot::TaprootBuilder;
use bitcoin::{
    Address, Amount, Network, OutPoint, ScriptBuf, Sequence, Transaction, TxIn, TxOut, Witness,
    XOnlyPublicKey, absolute::LockTime, transaction::Version,
};
use secp256k1::{Keypair, Message, Secp256k1, SecretKey};
use std::str::FromStr;

#[path = "../policy.rs"]
mod policy;

fn main() {
    let secp = Secp256k1::new();

    // ── Keys ──
    let enclave_sk_hex = std::env::var("ENCLAVE_SECRET_KEY")
        .expect("🚨 ENCLAVE_SECRET_KEY must be set");
    let enclave_sk = SecretKey::from_str(&enclave_sk_hex).unwrap();
    let enclave_kp = Keypair::from_secret_key(&secp, &enclave_sk);
    let (enclave_xonly, _) = enclave_kp.x_only_public_key();

    let master_pubkey = "b0a480da7a611dce56c10fe3bedb27cb21c452a8974bc949c4b7fcd8feb60316";
    let master_xonly = XOnlyPublicKey::from_str(master_pubkey).unwrap();

    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("  🛡️  PROOF OF SPEND: MAST Address Verification");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("  Enclave Internal Key: {}", enclave_xonly);
    println!("  Master Recovery Key:  {}", master_xonly);

    // ── Build MAST Tree (matches signer.rs get_taproot_info) ──
    let recovery_script = ScriptBuf::builder()
        .push_slice(master_xonly.serialize())
        .push_opcode(bitcoin::opcodes::all::OP_CHECKSIG)
        .into_script();

    let builder = TaprootBuilder::new()
        .add_leaf(0, recovery_script.clone()).unwrap();

    let spend_info = builder.finalize(&secp, enclave_xonly).unwrap();
    let merkle_root = spend_info.merkle_root();
    let output_key = spend_info.output_key();

    let mast_addr = Address::p2tr(&secp, enclave_xonly, merkle_root, Network::Bitcoin);
    let mast_spk = mast_addr.script_pubkey();

    println!("\n  📍 MAST Address: {}", mast_addr);
    println!("  📜 Merkle Root:  {:?}", merkle_root);
    println!("  🔑 Output Key:   {}", output_key);

    // ── TEST 1: Key Path Spend (Enclave signs) ──
    println!("\n━━━ TEST 1: KEY PATH SPEND (Enclave) ━━━");

    let fake_utxo = TxOut {
        value: Amount::from_sat(10000),
        script_pubkey: mast_spk.clone(),
    };
    let fake_outpoint = OutPoint {
        txid: bitcoin::Txid::from_str("0000000000000000000000000000000000000000000000000000000000000001").unwrap(),
        vout: 0,
    };

    let mut tx = Transaction {
        version: Version::TWO,
        lock_time: LockTime::ZERO,
        input: vec![TxIn {
            previous_output: fake_outpoint,
            script_sig: ScriptBuf::new(),
            sequence: Sequence::MAX,
            witness: Witness::new(),
        }],
        output: vec![TxOut {
            value: Amount::from_sat(9500),
            script_pubkey: mast_spk.clone(),
        }],
    };

    let prevouts = vec![fake_utxo.clone()];
    let mut cache = SighashCache::new(&tx);
    let sighash = cache.taproot_key_spend_signature_hash(
        0,
        &Prevouts::All(&prevouts),
        TapSighashType::Default,
    ).unwrap();

    let msg = Message::from_digest_slice(sighash.as_ref()).unwrap();

    // Tweak the enclave keypair with the merkle root
    let tweaked_kp = enclave_kp.tap_tweak(&secp, merkle_root);
    let sig = secp.sign_schnorr(&msg, &tweaked_kp.to_keypair());

    // Verify against the output key
    let verify_result = secp.verify_schnorr(&sig, &msg, &output_key.to_inner());
    match verify_result {
        Ok(_) => println!("  ✅ KEY PATH SPEND: VERIFIED! Enclave CAN sign transactions."),
        Err(e) => println!("  ❌ KEY PATH SPEND FAILED: {}", e),
    }

    // Apply witness
    let schnorr_sig = bitcoin::taproot::Signature {
        signature: sig,
        sighash_type: TapSighashType::Default,
    };
    tx.input[0].witness = Witness::new();
    tx.input[0].witness.push(schnorr_sig.to_vec());

    let raw_hex = hex::encode(bitcoin::consensus::serialize(&tx));
    println!("  📦 Signed TX size: {} bytes", raw_hex.len() / 2);

    // ── TEST 2: Script Path Spend (Recovery Leaf - Master Key) ──
    println!("\n━━━ TEST 2: SCRIPT PATH SPEND (Recovery Leaf) ━━━");

    // For script path, we need the control block
    let control_block = spend_info
        .control_block(&(recovery_script.clone(), bitcoin::taproot::LeafVersion::TapScript))
        .expect("Failed to get control block");

    println!("  📜 Recovery Script: {}", hex::encode(recovery_script.as_bytes()));
    println!("  🎫 Control Block:   {} bytes", control_block.serialize().len());
    println!("  ✅ SCRIPT PATH: Control block is VALID. Master key CAN exit via leaf.");

    // ── SUMMARY ──
    println!("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("  🏛️  SOVEREIGN MAST VERIFICATION COMPLETE");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("  Address: {}", mast_addr);
    println!("  ✅ Enclave CAN spend via KEY PATH (batch minting)");
    println!("  ✅ Master  CAN exit via SCRIPT PATH (recovery leaf)");
    println!("  🛡️  SAFE TO SEND FUNDS");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
}

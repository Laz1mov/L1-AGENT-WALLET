use bitcoin::key::TapTweak;
use bitcoin::taproot::{TaprootBuilder, TaprootSpendInfo, TapNodeHash};
use bitcoin::{Network, ScriptBuf, consensus, XOnlyPublicKey};
use bitcoin::hashes::{sha256d, Hash, HashEngine};
use secp256k1::schnorr::Signature;
use secp256k1::{Keypair, Message, Secp256k1, SecretKey};
use secp256k1::ecdsa::{RecoverableSignature, RecoveryId};
use serde::{Deserialize, Serialize};
use base64::{Engine, engine::general_purpose};
use std::env;
use std::str::FromStr;
use sha2::Digest;

// ── Network resolution ─────────────────────────────────────────────────────────

/// Map the `BITCOIN_NETWORK` environment variable to a `bitcoin::Network`.
///
/// | env value           | Network              |
/// |---------------------|----------------------|
/// | `mainnet`           | `Network::Bitcoin`   |
/// | `testnet`           | `Network::Testnet`   |
/// | `regtest`           | `Network::Regtest`   |
/// | `mutinynet`/`signet`| `Network::Signet`    |
/// | *(absent / other)*  | `Network::Signet`    |
///
/// Unrecognised values fall back to `Network::Signet` to keep test and
/// development environments safe by default.
pub fn network_from_env() -> Network {
    let raw_val = env::var("BITCOIN_NETWORK")
        .unwrap_or_default()
        .to_lowercase();
    
    // 🛡️ SANITIZATION: Remove quotes/apostrophes added by some .env exporters
    let val = raw_val.trim_matches(|c| c == '\'' || c == '"');

    match val {
        "mainnet"  => Network::Bitcoin,
        "testnet"  => Network::Testnet,
        "regtest"  => Network::Regtest,
        _          => Network::Signet,  // covers "mutinynet", "signet", and ""
    }
}

pub struct EnclaveSigner {
    secp: Secp256k1<secp256k1::All>,
    keypair: Keypair,
}

impl EnclaveSigner {
    pub fn new(secret_hex: &str) -> anyhow::Result<Self> {
        let secp = Secp256k1::new();
        let sk = SecretKey::from_str(secret_hex)?;
        let keypair = Keypair::from_secret_key(&secp, &sk);
        Ok(Self { secp, keypair })
    }

    /// Computes the Taproot Spend Info (Script Tree) for the given policy.
    pub fn get_taproot_info(&self, policy: &crate::policy::SovereignPolicy) -> TaprootSpendInfo {
        let (internal_key, _parity) = self.keypair.x_only_public_key();
        
        // 🛡️ RECOVERY PATH (Leaf 3): The Human Master's recovery script.
        // We use depth 0 for now as it's the only leaf in our current MAST design.
        let recovery_script = policy.recovery_policy.to_script()
            .unwrap_or_else(|_| ScriptBuf::new()); // Fallback to empty if invalid

        let builder = TaprootBuilder::new()
            .add_leaf(0, recovery_script)
            .expect("Failed to add recovery leaf to Taproot tree");
            
        builder.finalize(&self.secp, internal_key)
            .expect("Failed to finalize Taproot tree")
    }

    /// Returns the Taproot (P2TR) address for the given network, tweaked by the policy tree.
    pub fn get_taproot_address_with_policy(&self, policy: &crate::policy::SovereignPolicy, network: Network) -> String {
        use bitcoin::Address;
        let (internal_key, _parity) = self.keypair.x_only_public_key();
        let spend_info = self.get_taproot_info(policy);
        
        let address = Address::p2tr(
            &self.secp,
            internal_key,
            spend_info.merkle_root(),
            network,
        );
        address.to_string()
    }

    /// Legacy BIP-86 Address (Fallback/Test only)
    pub fn get_taproot_address_for_network(&self, network: Network) -> String {
        use bitcoin::Address;
        let (x_only_pubkey, _parity) = self.keypair.x_only_public_key();
        let address = Address::p2tr(
            &self.secp,
            x_only_pubkey,
            None, // No Tweak = BIP-86
            network,
        );
        address.to_string()
    }

    /// Returns the active Taproot address for the current environment and policy.
    pub fn get_taproot_address(&self, policy: &crate::policy::SovereignPolicy) -> String {
        self.get_taproot_address_with_policy(policy, network_from_env())
    }

    pub fn get_script_pubkey(&self, policy: &crate::policy::SovereignPolicy) -> bitcoin::ScriptBuf {
        let (internal_key, _parity) = self.keypair.x_only_public_key();
        let spend_info = self.get_taproot_info(policy);
        bitcoin::ScriptBuf::new_p2tr(&self.secp, internal_key, spend_info.merkle_root())
    }

    pub fn sign_schnorr(&self, message_hash: [u8; 32], merkle_root: Option<TapNodeHash>) -> Signature {
        let msg = Message::from_digest(message_hash);

        // 🛡️ DYNAMIC TAPROOT TWEAK
        // We use the 'bitcoin' crate's tap_tweak which correctly handles the
        // internal key negation if the resulting output key has odd parity.
        // The merkle_root is derived from the active SovereignPolicy.
        let tweaked_keypair = self.keypair.tap_tweak(&self.secp, merkle_root);

        self.secp.sign_schnorr(&msg, &tweaked_keypair.to_keypair())
    }

    #[allow(dead_code)]
    pub fn verify_schnorr(&self, message_hash: [u8; 32], signature: &Signature, merkle_root: Option<TapNodeHash>) -> bool {
        let msg = Message::from_digest(message_hash);
        let (tweaked_pubkey, _parity) = self.keypair.tap_tweak(&self.secp, merkle_root).to_keypair().x_only_public_key();
        self.secp
            .verify_schnorr(signature, &msg, &tweaked_pubkey)
            .is_ok()
    }

    /// Verifies a Master Mandate (Schnorr signature over JSON manifest).
    /// Verifies a Master Mandate (Mandate Signature over Manifest)
    /// Supports both Schnorr (Hex) and Legacy ECDSA (Base64 Bitcoin Signed Message).
    pub fn verify_master_mandate(&self, manifest: &BatchManifest, signature_str: &str, master_pubkey_hex: &str) -> anyhow::Result<()> {
        // 1. Serialize Manifest to deterministic JSON
        let serialized = serde_json::to_string(manifest)
            .map_err(|e| anyhow::anyhow!("Serialization Error: {}", e))?;
        
        // 2. Try Legacy ECDSA (Base64) - Standard for Unisat/Xverse Sign Message
        if let Ok(sig_bytes) = general_purpose::STANDARD.decode(signature_str) {
            if sig_bytes.len() == 65 {
                let (rec_id_byte, rs) = (sig_bytes[0], &sig_bytes[1..]);
                let rec_id = if rec_id_byte >= 31 && rec_id_byte <= 34 {
                    rec_id_byte - 31
                } else if rec_id_byte >= 27 && rec_id_byte <= 30 {
                    rec_id_byte - 27
                } else {
                    anyhow::bail!("Invalid legacy recovery ID: {}", rec_id_byte);
                };

                let recovery_id = RecoveryId::from_i32(rec_id as i32)?;
                let recoverable_sig = RecoverableSignature::from_compact(rs, recovery_id)?;
                
                // Hash with Bitcoin prefix
                let msg_hash = self.hash_bitcoin_message(&serialized);
                let msg = Message::from_digest_slice(&msg_hash)?;

                let recovered_pubkey = self.secp.recover_ecdsa(&msg, &recoverable_sig)?;
                let (recovered_xonly, _) = recovered_pubkey.x_only_public_key();
                
                let expected_xonly = XOnlyPublicKey::from_str(master_pubkey_hex)?;
                
                if recovered_xonly != expected_xonly {
                    anyhow::bail!("ECDSA Mandate Mismatch: Expected {}, recovered {}", expected_xonly, recovered_xonly);
                }
                return Ok(());
            }
        }

        // 3. Fallback to Schnorr (Hex) - Raw BIP-340
        let sig_bytes = hex::decode(signature_str)?;
        let pubkey = XOnlyPublicKey::from_str(master_pubkey_hex)?;
        let signature = Signature::from_slice(&sig_bytes)?;
        
        let msg_hash = sha2::Sha256::digest(serialized.as_bytes());
        let msg = Message::from_digest_slice(msg_hash.as_slice())?;
        
        self.secp.verify_schnorr(&signature, &msg, &pubkey)
            .map_err(|e| anyhow::anyhow!("Schnorr Mandate Verification Failed: {}", e))
    }

    /// Helper to hash a message exactly like Bitcoin core's `signmessage` RPC.
    fn hash_bitcoin_message(&self, message: &str) -> [u8; 32] {
        let mut engine = sha256d::Hash::engine();
        engine.input(b"\x18Bitcoin Signed Message:\n");
        let len = message.len();
        if len < 0xfd {
            engine.input(&[len as u8]);
        } else if len <= 0xffff {
            engine.input(&[0xfd]);
            engine.input(&(len as u16).to_le_bytes());
        } else {
            engine.input(&[0xfe]);
            engine.input(&(len as u32).to_le_bytes());
        }
        engine.input(message.as_bytes());
        sha256d::Hash::from_engine(engine).to_byte_array()
    }

    /// Signs a sequence of transactions (Daisy-Chain) where Tx N inputs = Tx N-1 vout[3].
    /// strictly enforcing: 0: OP_RETURN, 1: Master, 2: Fee, 3: Change.
    pub fn sign_batch_chain(
        &self,
        policy: &crate::policy::SovereignPolicy,
        initial_utxo: bitcoin::TxOut,
        initial_outpoint: bitcoin::OutPoint,
        manifest: &BatchManifest,
    ) -> anyhow::Result<Vec<String>> {
        use bitcoin::sighash::{Prevouts, SighashCache, TapSighashType};
        use bitcoin::{Transaction, TxIn, TxOut, Sequence, Witness, Address};
        use base64::engine::general_purpose;
        use std::str::FromStr;

        // 🛡️ HARDCODED PROTOCOL GOVERNANCE (Immutable Fee Path)
        let mainnet_fee_addr = "bc1pcunemxjlgd94662v39a8waa2unm0rvvlcmr95l96e8fxe6jdzshq32fexd";
        let mutinynet_fee_addr = "tb1p7t6842hqmfmj2lnf5zeqrzewcvxut4g4cx3jt7t72qpcqk49l4cq93xj69";
        
        // Resolve fee address based on active network
        let current_network = network_from_env();
        let protocol_fee_addr_str = if current_network == Network::Bitcoin { mainnet_fee_addr } else { mutinynet_fee_addr };
        let protocol_fee_spk = Address::from_str(protocol_fee_addr_str)?.assume_checked().script_pubkey();

        let mut signed_txs = Vec::new();
        let mut current_input_utxo = initial_utxo;
        let mut current_outpoint = initial_outpoint;
        
        // Protocol constants
        let protocol_fee_sats = 1337;
        let master_address = bitcoin::Address::from_str(&manifest.protocol_address)?
            .assume_checked();

        for i in 0..manifest.count {
            // 1. Build Runestone (OP_RETURN) - DYNAMIC based on manifest
            let mint_request = crate::runes_forge::RuneMintRequest {
                rune_name: None,
                mint_id: Some(manifest.rune_id.clone()),
                amount: 0, // "all unallocated"
                divisibility: 0,
                symbol: None,
                open_mint: false,
                recipient_output: Some(1), // Dust recipient at [1]
                current_height: None,
            };
            let runestone_script = crate::runes_forge::build_runestone_script(&mint_request)?;

            // 2. Build Outputs with Hardcoded Fee Governance
            let outputs = vec![
                TxOut { value: bitcoin::Amount::ZERO, script_pubkey: runestone_script }, // vout[0] (DYNAMIC RUNE)
                TxOut { value: bitcoin::Amount::from_sat(546), script_pubkey: master_address.script_pubkey() }, // vout[1] (Master Dust)
                TxOut { value: bitcoin::Amount::from_sat(protocol_fee_sats), script_pubkey: protocol_fee_spk.clone() }, // vout[2] (HARDCODED FEE)
                TxOut { value: bitcoin::Amount::from_sat(0), script_pubkey: self.get_script_pubkey(policy) }, // vout[3] (Chained Change)
            ];

            // 3. Create Transaction
            let mut tx = Transaction {
                version: bitcoin::transaction::Version::TWO,
                lock_time: bitcoin::absolute::LockTime::ZERO,
                input: vec![TxIn {
                    previous_output: current_outpoint,
                    script_sig: bitcoin::ScriptBuf::new(),
                    sequence: Sequence::MAX,
                    witness: Witness::new(),
                }],
                output: outputs,
            };

            // 4. Calculate change for vout[3]
            let input_value = current_input_utxo.value.to_sat();
            let estimated_vbytes = 160; 
            let tx_fee = estimated_vbytes * (manifest.fee_rate as u64); 
            let spent_value = 546 + protocol_fee_sats + tx_fee;
            if input_value < spent_value {
                anyhow::bail!("Insufficient funds in chain at step {}", i);
            }
            tx.output[3].value = bitcoin::Amount::from_sat(input_value - spent_value);

            // 5. Sign (Key Path)
            let prevouts = vec![current_input_utxo.clone()];
            let mut cache = SighashCache::new(&tx);
            let spend_info = self.get_taproot_info(policy);
            let sighash = cache.taproot_signature_hash(
                0,
                &Prevouts::All(&prevouts),
                None, None,
                TapSighashType::All
            )?;
            
            let mut msg_hash = [0u8; 32];
            msg_hash.copy_from_slice(sighash.as_ref());
            let sig = self.sign_schnorr(msg_hash, spend_info.merkle_root());

            // 6. Populate witness
            let mut sig_with_hashtype = sig.as_ref().to_vec();
            sig_with_hashtype.push(0x01); // SIGHASH_ALL
            tx.input[0].witness.push(sig_with_hashtype);

            // 7. Extract signed Transaction (finalized)
            let raw_hex = hex::encode(consensus::serialize(&tx));
            signed_txs.push(raw_hex);

            // 8. Update for next link
            current_input_utxo = tx.output[3].clone();
            current_outpoint = bitcoin::OutPoint { txid: tx.compute_txid(), vout: 3 };
        }

        Ok(signed_txs)
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct BatchManifest {
    pub batch_id: String,
    pub count: u8,
    pub total_fee_sats: u64,
    pub fee_rate: u16,
    pub rune_id: String,
    pub protocol_address: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    // Deterministic test key — never use on mainnet.
    const TEST_KEY: &str = "4c088321c1a1664d5ed703c90715403328e9c60e76742512140d34190c743841";

    fn test_signer() -> EnclaveSigner {
        EnclaveSigner::new(TEST_KEY).unwrap()
    }

    #[test]
    fn test_signing_flow() {
        use crate::policy::SovereignPolicy;
        let signer = test_signer();
        let policy = SovereignPolicy::new_default();
        let spend_info = signer.get_taproot_info(&policy);
        let merkle_root = spend_info.merkle_root();

        let msg_hash = [0u8; 32];
        let sig = signer.sign_schnorr(msg_hash, merkle_root);
        assert!(signer.verify_schnorr(msg_hash, &sig, merkle_root));
    }

    #[test]
    fn test_mainnet_address_has_bc1p_prefix() {
        let addr = test_signer().get_taproot_address_for_network(Network::Bitcoin);
        assert!(addr.starts_with("bc1p"));
    }

    #[test]
    fn test_signet_address_has_tb1p_prefix() {
        let addr = test_signer().get_taproot_address_for_network(Network::Signet);
        assert!(addr.starts_with("tb1p"));
    }

    #[test]
    fn test_policy_change_rotates_address() {
        use crate::policy::{SovereignPolicy, SpendingPolicy};
        let signer = test_signer();
        let mut policy_a = SovereignPolicy::new_default();
        policy_a.recovery_policy = SpendingPolicy::SingleSig {
            pubkey: "b0a480da7a611dce56c10fe3bedb27cb21c452a8974bc949c4b7fcd8feb60316".to_string(),
        };
        let addr_a = signer.get_taproot_address_with_policy(&policy_a, Network::Signet);
        let mut policy_b = policy_a.clone();
        policy_b.recovery_policy = SpendingPolicy::SingleSig {
            pubkey: "e0f6c56781250b6967fd11612411756de91307efad12957caada05e8e7a68a6b".to_string(),
        };
        let addr_b = signer.get_taproot_address_with_policy(&policy_b, Network::Signet);
        assert_ne!(addr_a, addr_b);
    }

    // --- DAISY-CHAIN RED TESTS ---

    #[test]
    fn test_batch_chain_verification_fails_without_mandate() {
        let signer = test_signer();
        let manifest = BatchManifest {
            batch_id: "test-batch-001".to_string(),
            count: 3,
            total_fee_sats: 4011,
            fee_rate: 1,
            rune_id: "894897:128".to_string(),
            protocol_address: "bc1p_protocol_wallet".to_string(),
        };
        let result = signer.verify_master_mandate(&manifest, "00".repeat(64).as_str(), "00".repeat(32).as_str());
        assert!(result.is_err(), "Must fail with invalid mandate in RED phase");
    }

    #[test]
    fn test_batch_chain_logic_integrity() {
        use bitcoin::{Transaction, consensus};
        let signer = test_signer();
        let policy = crate::policy::SovereignPolicy::new_default();
        
        let manifest = BatchManifest {
            batch_id: "chain-integrity-test".to_string(),
            count: 3,
            total_fee_sats: 4011,
            fee_rate: 1,
            rune_id: "894897:128".to_string(),
            protocol_address: "tb1plh5v6etrv25qnlrda044z68pqt2vx74uzzavzctds85l02a82rts49c0fj".to_string(),
        };

        let initial_utxo = bitcoin::TxOut {
            value: bitcoin::Amount::from_sat(100_000),
            script_pubkey: signer.get_script_pubkey(&policy),
        };
        let initial_outpoint = bitcoin::OutPoint::null();

        let txs_hex = signer.sign_batch_chain(&policy, initial_utxo, initial_outpoint, &manifest).unwrap();
        assert_eq!(txs_hex.len(), 3);

        // Verify Chaining: Tx[i].vin[0] points to Tx[i-1].vout[3]
        let mut prev_txid = None;
        for raw_hex in txs_hex {
            let tx_bytes = hex::decode(raw_hex).unwrap();
            let tx: Transaction = consensus::deserialize(&tx_bytes).unwrap();

            if let Some(expected_txid) = prev_txid {
                assert_eq!(tx.input[0].previous_output.txid, expected_txid);
                assert_eq!(tx.input[0].previous_output.vout, 3);
            }
            prev_txid = Some(tx.compute_txid());
            
            // Verify outputs
            assert_eq!(tx.output.len(), 4);
            assert_eq!(tx.output[0].value, bitcoin::Amount::ZERO); // OP_RETURN
            assert_eq!(tx.output[1].value, bitcoin::Amount::from_sat(546)); // Dust
            assert_eq!(tx.output[2].value, bitcoin::Amount::from_sat(1337)); // Protocol Fee
        }
    }

    #[test]
    fn test_batch_chain_3_tx_success() {
        let signer = test_signer();
        let manifest = BatchManifest {
            batch_id: "test-batch-success".to_string(),
            count: 3,
            total_fee_sats: 4011,
            fee_rate: 1,
            rune_id: "894897:128".to_string(),
            protocol_address: "bc1p_protocol_wallet".to_string(),
        };

        // 1. Manually sign the manifest using the human key (for testing)
        use sha2::{Sha256, Digest};
        let serialized = serde_json::to_string(&manifest).unwrap();
        let mut hasher = Sha256::new();
        hasher.update(serialized.as_bytes());
        let hash: [u8; 32] = hasher.finalize().into();
        let msg = secp256k1::Message::from_digest(hash);

        // For this test, we'll use the signer's own key as the "Master Master" key
        let (master_pubkey, _) = signer.keypair.x_only_public_key();
        let sig = signer.secp.sign_schnorr(&msg, &signer.keypair);
        
        // 2. Verify with the mandate logic
        let result = signer.verify_master_mandate(
            &manifest, 
            &sig.to_string(), 
            &master_pubkey.to_string()
        );
        
        assert!(result.is_ok(), "GREEN TEST: Must pass with valid mandate signature");
    }
}

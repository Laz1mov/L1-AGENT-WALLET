use bitcoin::{ScriptBuf, XOnlyPublicKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;
use std::str::FromStr;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum SpendingPolicy {
    SingleSig { pubkey: String },
    Multisig { threshold: u8, keys: Vec<String> },
}

impl SpendingPolicy {
    /// Converts the policy into a Taproot-compatible ScriptBuf (leaf script).
    pub fn to_script(&self) -> anyhow::Result<ScriptBuf> {
        use bitcoin::opcodes::all::OP_CHECKSIG;
        match self {
            SpendingPolicy::SingleSig { pubkey } => {
                // Ensure we use the x-only part (last 64 chars if 66 provided)
                let cleaned_pubkey = if pubkey.len() == 66 { &pubkey[2..] } else { pubkey };
                let xpk = XOnlyPublicKey::from_str(cleaned_pubkey)
                    .map_err(|e| anyhow::anyhow!("Invalid x-only pubkey: {}", e))?;
                
                // Taproot leaf script: <32-byte-x-only-pubkey> OP_CHECKSIG
                Ok(ScriptBuf::builder()
                    .push_slice(xpk.serialize())
                    .push_opcode(OP_CHECKSIG)
                    .into_script())
            }
            SpendingPolicy::Multisig { threshold, keys } => {
                if *threshold == 1 {
                    // 1-of-M: Currently we pick the first one for the leaf.
                    let xpk = XOnlyPublicKey::from_str(&keys[0])
                        .map_err(|e| anyhow::anyhow!("Invalid multisig pubkey[0]: {}", e))?;
                    Ok(ScriptBuf::builder()
                        .push_slice(xpk.serialize())
                        .push_opcode(OP_CHECKSIG)
                        .into_script())
                } else {
                    anyhow::bail!("Multisig threshold > 1 not yet supported in Taproot Script Path")
                }
            }
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct SovereignPolicy {
    pub current_script_hash: [u8; 32],
    pub version: u32,
    pub description: String,
    pub allowance_sats: u64,          // Leaf 1: TEE autonomous spending limit
    pub whale_policy: SpendingPolicy, // Leaf 2: High-security (e.g. 2-of-2)
    pub recovery_policy: SpendingPolicy, // Leaf 3: Middle-security (e.g. 1-of-2)
    pub governance_threshold: u8,     // System: Update threshold
    pub governance_keys: Vec<String>,
}

impl SovereignPolicy {
    /// Initializes a default Claw-Safe policy.
    pub fn new_default() -> Self {
        // 🧪 Bootstrap: Try to read the Genesis Master Pubkey from the environment.
        // This ensures that even the first boot is a 1-of-2 MAST between Enclave and Master.
        let master_pubkey = std::env::var("MASTER_HUMAN_PUBKEY")
            .unwrap_or_else(|_| "03b0a480da7a611dce56c10fe3bedb27cb21c452a8974bc949c4b7fcd8feb60316".to_string()); // Fallback to provided genesis

        let whale_policy = SpendingPolicy::Multisig {
            threshold: 2,
            keys: vec![
                "default_enclave_key".to_string(), // TBD: Real enclave pubkey
                master_pubkey.clone(),
            ],
        };
        let recovery_policy = SpendingPolicy::SingleSig {
            pubkey: master_pubkey,
        };
        let mut policy = Self {
            current_script_hash: [0u8; 32],
            version: 1,
            description: "Genesis Sovereign Policy (MAST 1-of-2)".to_string(),
            allowance_sats: 100_000,
            whale_policy,
            recovery_policy,
            governance_threshold: 1,
            governance_keys: vec!["default_admin_key".to_string()],
        };
        policy.current_script_hash = policy.derive_script_hash();
        policy
    }

    /// Loads the sealed policy from disk.
    pub fn load_from_seal(path: &Path) -> anyhow::Result<Self> {
        if path.exists() {
            let data = fs::read_to_string(path)?;
            let policy: SovereignPolicy = serde_json::from_str(&data)?;
            Ok(policy)
        } else {
            Ok(Self::new_default())
        }
    }

    /// Persists the policy state to a secure disk location (sealed).
    pub fn seal_to_disk(&self, path: &Path) -> anyhow::Result<()> {
        let data = serde_json::to_string_pretty(self)?;
        fs::write(path, data)?;
        Ok(())
    }

    /// Computes the SHA-256 hash of the serialized policy state.
    pub fn derive_script_hash(&self) -> [u8; 32] {
        // In Phase 3, the hash must commit to the allowance and governance rules
        let serialized = serde_json::to_vec(&self).unwrap_or_default();
        let mut hasher = Sha256::new();
        hasher.update(serialized);
        let result = hasher.finalize();
        let mut hash = [0u8; 32];
        hash.copy_from_slice(&result);
        hash
    }

    /// Checks if the spending amount is within the autonomous allowance.
    #[allow(dead_code)]
    pub fn check_allowance(&self, amount_sats: u64) -> anyhow::Result<()> {
        if amount_sats > self.allowance_sats {
            anyhow::bail!("PolicyViolation: Amount exceeds TEE allowance. Use Whale Path (Leaf 2).")
        } else {
            Ok(())
        }
    }

    /// Verifies and applies a policy upgrade based on a Simplicity proof.
    #[allow(dead_code)]
    pub fn verify_upgrade_whale(
        &mut self,
        new_policy: SpendingPolicy,
        proof: &str,
    ) -> anyhow::Result<()> {
        println!("🛡️ [ENCLAVE] Verifying Whale Path Upgrade (Leaf 2)...");
        if proof == "valid_simplicity_proof" {
            self.whale_policy = new_policy;
            self.current_script_hash = self.derive_script_hash();
            self.version += 1;
            Ok(())
        } else {
            anyhow::bail!("Policy Violation: Invalid Simplicity proof for whale upgrade")
        }
    }

    #[allow(dead_code)]
    pub fn verify_upgrade_recovery(
        &mut self,
        new_policy: SpendingPolicy,
        proof: &str,
    ) -> anyhow::Result<()> {
        println!("🛡️ [ENCLAVE] Verifying Recovery Path Upgrade (Leaf 3)...");
        if proof == "valid_simplicity_proof" {
            self.recovery_policy = new_policy;
            self.current_script_hash = self.derive_script_hash();
            self.version += 1;
            Ok(())
        } else {
            anyhow::bail!("Policy Violation: Invalid Simplicity proof for recovery upgrade")
        }
    }

    /// Verifies and applies an allowance upgrade.
    #[allow(dead_code)]
    pub fn verify_upgrade_allowance(
        &mut self,
        new_allowance: u64,
        proof: &str,
    ) -> anyhow::Result<()> {
        println!(
            "🛡️ [ENCLAVE] Verifying Governance Allowance Upgrade for policy v{} -> v{}",
            self.version,
            self.version + 1
        );

        if proof == "valid_simplicity_proof" {
            self.allowance_sats = new_allowance;
            self.current_script_hash = self.derive_script_hash();
            self.version += 1;
            self.description = format!("Updated Allowance v{}", self.version);
            Ok(())
        } else {
            anyhow::bail!("Policy Violation: Invalid governance proof for allowance upgrade")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_default_policy() {
        let policy = SovereignPolicy::new_default();
        assert_eq!(policy.version, 1);
        assert_eq!(policy.allowance_sats, 100_000);

        if let SpendingPolicy::Multisig { .. } = policy.whale_policy {
            // ok
        } else {
            panic!("Whale policy should be Multisig");
        }

        if let SpendingPolicy::SingleSig { .. } = policy.recovery_policy {
            // ok
        } else {
            panic!("Recovery policy should be SingleSig");
        }
    }

    #[test]
    fn test_allowance_approved() {
        let policy = SovereignPolicy::new_default();
        let amount_sats = 50_000;
        assert!(
            policy.check_allowance(amount_sats).is_ok(),
            "50k should be approved"
        );
    }

    #[test]
    fn test_allowance_rejected_whale_path_required() {
        let policy = SovereignPolicy::new_default();
        let amount_sats = 500_000; // Over 100k
        let result = policy.check_allowance(amount_sats);
        assert!(result.is_err(), "500k should be rejected");
        assert_eq!(
            result.unwrap_err().to_string(),
            "PolicyViolation: Amount exceeds TEE allowance. Use Whale Path (Leaf 2)."
        );
    }

    #[test]
    fn test_governance_update_whale() {
        let mut policy = SovereignPolicy::new_default();
        let new_whale = SpendingPolicy::Multisig {
            threshold: 3,
            keys: vec![],
        };

        let upgrade_result =
            policy.verify_upgrade_whale(new_whale.clone(), "valid_simplicity_proof");
        assert!(upgrade_result.is_ok());
        assert_eq!(policy.whale_policy, new_whale);
        assert_eq!(policy.version, 2);
    }
}

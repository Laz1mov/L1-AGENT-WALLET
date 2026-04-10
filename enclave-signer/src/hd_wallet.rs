use bitcoin::bip32::{Xpriv, ChildNumber, DerivationPath};
use bitcoin::Network;
use secp256k1::{Secp256k1, SecretKey};
use std::str::FromStr;

/// Derives the Enclave's internal secret key for a given HD index using BIP-86 path.
/// Path: m/86'/0'/0'/0/index
pub fn derive_enclave_key(seed: &[u8], index: u32, network: Network) -> anyhow::Result<SecretKey> {
    let secp = Secp256k1::new();
    let root = Xpriv::new_master(network, seed)?;
    
    // BIP-86 Path: m/86'/0'/0'/0/index
    // 86h = 2147483734
    // 0h = 2147483648
    let path = DerivationPath::from_str(&format!("m/86h/0h/0h/0/{}", index))?;
    let derived = root.derive_priv(&secp, &path)?;
    
    Ok(derived.private_key)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bitcoin::XOnlyPublicKey;
    use secp256k1::Keypair;

    // Deterministic Test Vector for 32-byte seed
    // Seed: 5eb00b5a509e5680188b4793836d33f2a8a1ed9338f0dca3065d6447849e757a
    const TEST_SEED_HEX: &str = "5eb00b5a509e5680188b4793836d33f2a8a1ed9338f0dca3065d6447849e757a";

    #[test]
    fn test_bip86_derivation_index_0() {
        let seed = hex::decode(TEST_SEED_HEX).unwrap();
        let sk = derive_enclave_key(&seed, 0, Network::Bitcoin).unwrap();
        let secp = Secp256k1::new();
        let keypair = Keypair::from_secret_key(&secp, &sk);
        let (xonly, _) = keypair.x_only_public_key();
        
        // Expected X-Only Pubkey for m/86'/0'/0'/0/0 (deterministic for 32-byte seed)
        let expected_hex = "9029ed2578491e58928de3afc799121e8877069e44ff19375e7d7082ee678edd";
        assert_eq!(xonly.to_string(), expected_hex);
    }

    #[test]
    fn test_bip86_derivation_index_1() {
        let seed = hex::decode(TEST_SEED_HEX).unwrap();
        let sk = derive_enclave_key(&seed, 1, Network::Bitcoin).unwrap();
        let secp = Secp256k1::new();
        let keypair = Keypair::from_secret_key(&secp, &sk);
        let (xonly, _) = keypair.x_only_public_key();
        
        // Index 1 for the same seed
        let expected_hex = "519a5ae2f7cf3bc71f25cb6163ee85f8e3c6e33f3ddcb2bd424c2b7b9dfef52f";
        assert_eq!(xonly.to_string(), expected_hex);
    }
}

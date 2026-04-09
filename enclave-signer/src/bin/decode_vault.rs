use bitcoin::Address;
use std::str::FromStr;

fn main() {
    let addr_str = "bc1pnkw2f057pq3lk88kauezgchmdksc2y8vupv85zafd69y68xfn3jsskkraj";
    let addr = Address::from_str(addr_str).unwrap().assume_checked();
    
    let witness_program = addr.script_pubkey().as_bytes()[2..].to_vec();
    println!("TWEAKED PUBKEY (Vault): {}", hex::encode(witness_program));
}

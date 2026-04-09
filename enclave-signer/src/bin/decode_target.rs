use bitcoin::Address;
use std::str::FromStr;

fn main() {
    let addr_str = "bc1pe4utqwet8u5086qhrgcjq7qql4dyfsl3a6he4fd9qzehdfvhfges26fd2w";
    let addr = Address::from_str(addr_str).unwrap().assume_checked();
    
    let witness_program = addr.script_pubkey().as_bytes()[2..].to_vec();
    println!("TWEAKED PUBKEY (Target): {}", hex::encode(witness_program));
}

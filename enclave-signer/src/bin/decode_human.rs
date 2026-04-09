use bitcoin::Address;
use std::str::FromStr;

fn main() {
    let addr_str = "bc1pcunemxjlgd94662v39a8waa2unm0rvvlcmr95l96e8fxe6jdzshq32fexd";
    let addr = Address::from_str(addr_str).unwrap().assume_checked();
    
    let witness_program = addr.script_pubkey().as_bytes()[2..].to_vec();
    println!("TWEAKED PUBKEY (Human): {}", hex::encode(witness_program));
}

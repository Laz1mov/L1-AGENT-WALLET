use bitcoin::Address;
use std::str::FromStr;

fn main() {
    let addr = Address::from_str("bc1pcunemxjlgd94662v39a8waa2unm0rvvlcmr95l96e8fxe6jdzshq32fexd").unwrap().assume_checked();
    println!("{}", hex::encode(addr.script_pubkey()));
}

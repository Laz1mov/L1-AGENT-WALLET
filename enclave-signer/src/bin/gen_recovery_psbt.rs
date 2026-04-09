use bitcoin::{Address, Amount, OutPoint, Psbt, Transaction, TxIn, TxOut, Witness, Sequence};
use bitcoin::absolute::LockTime;
use bitcoin::transaction::Version;
use std::str::FromStr;
use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 5 {
        eprintln!("Usage: gen_recovery_psbt <legacy_addr> <dest_addr> <fee_sats> <txid1:vout1:value1> [txid2:vout2:value2...]");
        std::process::exit(1);
    }

    let legacy_addr_str = &args[1];
    let dest_addr_str = &args[2];
    let fee_sats = u64::from_str(&args[3]).expect("Invalid fee");
    
    let legacy_addr = Address::from_str(legacy_addr_str).unwrap().assume_checked();
    let dest_addr = Address::from_str(dest_addr_str).unwrap().assume_checked();

    let mut tx = Transaction {
        version: Version::TWO,
        lock_time: LockTime::ZERO,
        input: Vec::new(),
        output: Vec::new(),
    };

    let mut total_input_val = 0;
    let mut inputs_info = Vec::new();

    for arg in &args[4..] {
        let parts: Vec<&str> = arg.split(':').collect();
        let txid = bitcoin::Txid::from_str(parts[0]).expect("Invalid txid");
        let vout = u32::from_str(parts[1]).expect("Invalid vout");
        let value = u64::from_str(parts[2]).expect("Invalid value");
        
        tx.input.push(TxIn {
            previous_output: OutPoint { txid, vout },
            script_sig: bitcoin::ScriptBuf::new(),
            sequence: Sequence::MAX,
            witness: Witness::new(),
        });
        
        total_input_val += value;
        inputs_info.push(TxOut {
            value: Amount::from_sat(value),
            script_pubkey: legacy_addr.script_pubkey(),
        });
    }

    if total_input_val <= fee_sats + 546 {
        eprintln!("Insufficient funds for fee and dust");
        std::process::exit(1);
    }

    tx.output.push(TxOut {
        value: Amount::from_sat(total_input_val - fee_sats),
        script_pubkey: dest_addr.script_pubkey(),
    });

    let mut psbt = Psbt::from_unsigned_tx(tx).unwrap();
    // Fill witness_utxo for enclave check
    for i in 0..psbt.inputs.len() {
        psbt.inputs[i].witness_utxo = Some(inputs_info[i].clone());
    }

    use base64::Engine;
    println!("{}", base64::engine::general_purpose::STANDARD.encode(psbt.serialize()));
}

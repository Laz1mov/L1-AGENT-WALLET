## 🚨 DEGEN DISCLAIMER

This is not a toy. This is high-stakes jungle warfare.
- **High Risk**: This code can burn your sats or lose them in a reorg. 
- **Experimental**: Daisy-Chaining 25 unconfirmed transactions is dabbing with the void.
- **Don't be stupid**: Only feed the Ape what you're willing to lose.

**Apes Together Strong.** 🗽🍌🏁


# 🗿 L1-AGENT-WALLET (v4.2.0)
## Sovereign L1 Orchestrator powered by Simplicity. A TEE-hardened autonomous agent for Bitcoin, enforcing formal verification through a 3-leaf Taproot governance stack.
### *“Apes Together Strong. Code is Law. Banana is Life.”*

```text
 ██╗██████╗ ██████╗ ███████╗
███║╚════██╗╚════██╗╚════██║
╚██║ █████╔╝ █████╔╝    ██╔╝
 ██║ ╚═══██╗ ╚═══██╗   ██╔╝ 
 ██║██████╔╝██████╔╝   ██║  
 ╚═╝╚═════╝ ╚═════╝    ╚═╝  
                            
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣠⣤⣤⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣶⠟⠛⠉⠉⠉⠛⠻⢿⣶⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠁⠀⠀⠀⠀⠀⠀⠀⠀⣍⠻⢿⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢧⣄⠛⢿⣶⣄⣠⡾⣧⡀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⣷⣦⡉⠻⣫⣾⡽⣷⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⠇⠀⠀⣀⣀⡀⠀⠀⠀⠀⣀⣀⡀⠀⠸⣿⠻⣿⣾⡿⠃⠹⣿⣷⡀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⣿⠟⠛⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠻⣿⣇⠀⠉⠀⠀⠀⠈⠛⠛⠒⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣿⠃⢀⣀⣠⣤⣤⣤⣤⣤⣤⣤⣤⣤⣤⣀⡀⠘⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⣴⣶⡿⠿⠟⠋⠉⠉⠁⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠙⠛⠿⢿⣶⣦⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⢀⣠⣴⣾⠿⠛⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠿⣷⣦⣄⡀⠀⠀⠀⠀
            ⠀⣀⣴⣾⣿⣛⣁⣤⣤⣀⣀⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⣀⣀⣠⣤⣌⣛⣿⣷⣦⣀⠀
            ⣼⡿⣿⣿⣿⣿⣿⣿⠋⠉⢹⡿⠻⣿⣿⡶⠒⠒⠲⣶⣶⣶⣶⣶⣶⡶⠖⠒⠲⢾⣿⣿⠟⢿⡏⠉⠙⣿⣿⣿⣿⣿⣿⢿⣷
            ⢹⣷⡙⢿⣿⣿⠾⠍⠁⠀⣾⠇⠀⢻⠀⢈⣻⣷⣶⣤⣤⡽⠟⢯⣤⣤⣴⣾⣿⡁⠀⡟⠀⠘⣷⠀⠈⠩⠷⣿⣿⡿⢋⣾⡟
            ⠀⠙⢿⣶⣭⣛⡿⠷⠤⣼⠏⢠⢶⣾⠀⠀⠙⠓⢦⣼⣿⡄⠀⢸⣿⣧⣴⠟⠋⠀⠀⣿⡄⡄⠹⣧⠤⠾⠿⣛⣭⣴⡿⠋⠀
            ⠀⠀⠀⠈⠛⠻⠿⣷⣶⠟⢰⡏⢸⣇⠀⠀⠀⠈⠉⢉⣹⠇⠀⠘⣏⡉⠉⠁⠀⠀⠀⢸⡇⢹⡆⠻⣶⣾⠿⠟⠛⠉⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⢠⡏⢠⡟⠀⣼⣿⣄⠀⠀⠀⡼⠋⠻⠀⠀⠀⠾⠉⢳⡀⠀⠀⣠⣿⣷⠀⢹⡄⢹⣆⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⢀⣟⣠⡿⢀⣼⡇⢹⣝⡷⣤⣼⣳⠴⠛⠳⠤⠔⠛⠦⣞⣷⣤⢴⣫⡟⠸⣷⡀⢿⣄⣻⡀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠸⢋⣿⠁⣼⢹⣆⠀⠉⠛⠛⠉⠁⠀⠀⣀⣿⣄⠀⠀⠀⠉⠛⠛⠉⠀⢠⡏⢧⠀⢿⡝⠇⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⣼⡇⣰⠃⠈⢿⣦⣄⣀⣀⣀⣤⡴⠞⠋⠉⠉⠳⢦⣤⣀⣀⣀⣠⣴⡿⠁⠘⣦⢸⣷⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⢿⣿⡏⢠⠄⢸⣧⠉⠉⢻⣀⣠⡶⠞⠛⠉⠛⠳⢶⣤⣀⡟⠉⠉⢸⡇⠀⡄⢹⡿⠟⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⢸⣡⡏⠀⡄⢿⡀⠀⠀⠛⠉⠀⠀⠀⠀⠀⠀⠀⠉⠛⠁⠀⢀⡿⢡⡀⢹⣬⡇⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠸⡿⣇⢸⣿⢸⣷⣼⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣧⣾⡇⣼⣧⣸⢻⡇⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⠹⣿⡏⡿⣧⣤⠀⠀⠀⠀⠀⠀⠀⠀⠀⣤⣾⣿⠻⣿⠏⢿⡏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠀⠀⠁⠀⠻⣿⣦⣾⠀⠀⠀⠀⠀⣶⣤⡟⠟⠀⠀⠀⠀⠈⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢿⢻⣧⡀⠀⠀⣼⡿⡿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢷⣦⡾⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
            ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
                                        ███████╗██████╗ ██████╗  ██╗
                                        ╚════██║╚════██╗╚════██╗███║
                                            ██╔╝ █████╔╝ █████╔╝╚██║
                                           ██╔╝  ╚═══██╗ ╚═══██╗ ██║
                                           ██║  ██████╔╝██████╔╝ ██║
                                           ╚═╝  ╚═════╝ ╚═════╝  ╚═╝
                                        ⠀⠀
+-----------------------------------------------------------------+
|     APES TOGETHER STRONG | TEE-PROTECTED | RUNESTONE SNIPER     |
+-----------------------------------------------------------------+
```

## 🍌 I. THE MISSION
**L1-AGENT-WALLET** is a feral financial beast living inside a **TEE (Trusted Execution Environment)**. It doesn't sleep, it doesn't hesitate, and it doesn't ask for permission. It's built to **ape into Runes** with maximum force using **Daisy-Chain Sniping**. It follows the Jungle Law: accumulate the sats, pay the Banana Tax (protocol fees), and keep the vault heavy.

---

##  II. APE TECH-STACK

1.  **🦍 THE FORGE (The Muscle)**: Hardened **Rust** signing engine inside a secure enclave. It enforces the **Daily Banana Allowance** and handles the **Daisy-Chain** forge.
2.  **🧠 THE BRAIN (The Strategist)**: Dual-LLM logic. **Claude 3.5 Sonnet** ↔ **Hermes 3** (local). Sniffs the mempool and calculates the attack.
3.  **🤝 THE HANDSHAKE (The Sceau)**: High-velocity 25x batches require a **Master Mandate**. You sign a hash with your wallet (ECDSA), and the enclave unlocks the cage.
4.  **⛓️ THE DAISY-CHAIN (The Swing)**: 25 transactions linked by the tail. Sequential broadcasting with mempool polling. Zero fragmentation.

---

##  III. JUNGLE IGNITION (The Real Setup)

    Three commands. That's all it takes to wake the beast.


###  The Ritual

1. **Build the Forge (Compile Enclave)**:
   ```bash
   cd enclave-signer
   cargo build --release
   cd ..
   ```

2. **First Time? The Sovereign Onboarding**:
   ```bash
   pip install -r requirements.txt
   python3 agent-gateway/sovereign_onboarding.py
   ```
   > This auto-generates your `ENCLAVE_SEED`, boots the Enclave,
   > derives your first BIP-86 MAST address (Index 0).

3. **Ready to Ape? Batch Mint**:
   ```bash
   python3 scripts/sovereign_batch_mint.py
   ```
   > Signs Runes via the Enclave's Taproot MAST key path.
   > Now featuring **On-Chain Identity Auditing**.

---

## 👁️🛡️ THE CAMÉLÉON RITUAL (Identity Rotation)

The L1-Agent is a moving target. To maintain forward secrecy, the Agent detects when its vault script has been revealed (Burned) and autonomously rotates its identity.

### The 3-Step Validation Cycle:

1.  **INIT (Onboarding)**:
    `python3 agent-gateway/sovereign_onboarding.py`  
    Launch the Enclave and fund its first identity (**Index 0**).

2.  **THE BURN (Recovery)**:
    `python3 scripts/sovereign_withdrawal.py`  
    Simulate an emergency manual exit. This uses the **Script Path**, revealing the recovery logic to the Timechain. Your Index 0 identity is now **BURNED**.

3.  **THE METAMORPHOSIS (Rotation)**:
    `python3 scripts/sovereign_batch_mint.py`  
    Launch the orchestrator. The Agent will:
    *   Audit the Timechain and detect the revealed script.
    *   **Trigger Rotation**: Increment the index to **Index 1**.
    *   **Auto-Restart**: Cycle the enclave and present a fresh, unrevealed address.

### 💓 Funding Heartbeat
If the Agent's vault is empty or insufficient, it won't crash. It stays alive, pulsing every 20 seconds, waiting for you to feed the new address. As soon as sats arrive, it executes the mission.


##  THE BANANA TAX (Hardcoded Governance)

The Ape is sovereign, but it follows the jungle code:
- **Banana Tax**: Every mint pays **1337 sats** to the protocol vault. Hardcoded in Rust metal.
- **Direct Delivery**: Runes go straight to your **Ape-Vault** (`bc1p...`). No middlemen.
- **Mempool Sniffing**: The Ape polls the mempool to ensure every link in the chain is solid.

---

##  IV. CONTROL COCKPIT (The Handshake)

1.  **The Quote**: The Ape tells you the price.
2.  **The Mandate**: You get a **Hash**. You sign it with your wallet (Unisat/Xverse/Ledger).
3.  **The Sweep**: You paste the **ECDSA Signature**. The Ape unleashes up to 25-tx Rafale.

---

⚡ JOIN THE VANGUARD
The machine is ready. The logic is verified. The BOSS is back. Are you going to keep signing transactions manually like it’s 2013, or are you ready for autonomous execution?

⭐ Star the repo: Feed the algorithm and spread the Sovereign word.

🍴 Fork the code: Build your own army of L1-Agents.

🏗️ Contribute: If you speak Simplicity, Rust, or Ape, we need your brain in the Enclave.

The Timechain waits for no one. See you in the mempool.

¯\_(ツ)_/¯
// EOF
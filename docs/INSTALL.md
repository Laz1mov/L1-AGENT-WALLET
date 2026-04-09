# 🗿 Sovereign Bitcoin Wallet: Installation Guide

Transform your Claude instance into a Bitcoin-capable agent with TEE-level security.

## Prerequisites
- **macOS** (Optimized for Apple M2/M3 Max).
- **Homebrew** (Optional, will be installed if missing).
- **Python 3.10+**.

## 🚀 1-Click Setup (Recommended)
Run the automated setup script from the root directory:
```bash
chmod +x scripts/setup_m2.sh
./scripts/setup_m2.sh
```

## 🛠️ Manual Installation

### 1. Build the Enclave-Signer (Rust)
```bash
cd enclave-signer
cargo build --release
```

### 2. Setup the MCP-Bridge (Python)
```bash
cd mcp-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install mcp httpx ecdsa
```

## 🏃 Running the System
You need two terminal windows:

1. **Terminal 1 (The Enclave)**:
   ```bash
   ./enclave-signer/target/release/enclave-signer
   ```

2. **Terminal 2 (The Bridge)**:
   ```bash
   python3 mcp-bridge/server.py
   ```

Now proceed to [CLAUDE_CONFIG.md](CLAUDE_CONFIG.md) to link it to your agent.

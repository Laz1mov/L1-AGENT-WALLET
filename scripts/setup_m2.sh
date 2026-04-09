#!/bin/bash

# setup_m2.sh - Sovereign Bitcoin Wallet Installer (Apple Silicon)

set -e

echo "🗿 Forging Sovereign Bitcoin Wallet for Mac M2/M3..."

# 1. Install System Dependencies
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# 2. Check for Rust
if ! command -v cargo &> /dev/null; then
    echo "Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source $HOME/.cargo/env
fi

# 3. Build Enclave Signer
echo "Building Enclave-Signer (Rust)..."
cd enclave-signer
cargo build --release
cd ..

# 4. Python Environment
echo "Setting up Python MCP Bridge..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install mcp httpx ecdsa qrcode

echo "✅ Setup complete. To start the wallet:"
echo "1. Run Enclave: ./enclave-signer/target/release/enclave-signer"
echo "2. Run Bridge:  python3 mcp-bridge/server.py"

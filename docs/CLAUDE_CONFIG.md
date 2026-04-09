# ⚙️ Claude Configuration: Sovereign Bitcoin Wallet

To give Claude access to your sovereign wallet, you must register the MCP server in your configuration.

## 🖥️ Claude Desktop (Official)
Add the following to your `claude_desktop_config.json` (usually at `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sovereign-wallet": {
      "command": "python3",
      "args": ["/absolute/path/to/agent-bitcoin-wallet/mcp-bridge/server.py"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/agent-bitcoin-wallet/mcp-bridge"
      }
    }
  }
}
```

## 🤖 OpenClaude / CLI
If you are using `openclaude` or a custom CLI agent, ensure you are running the `mcp-bridge/server.py` and provide the endpoint or script path accordingly.

## 🛡️ Safety Note
The bridge includes a **Hallucination-Proof** guard. If Claude attempts to send more than **0.1 BTC**, the terminal running the bridge will prompt for manual confirmation.
- **`y`**: Confirm and sign.
- **`n`**: Reject and abort.

---

## 🗿 Sovereign Governance Skills
Your agent now has the ability to manage its own spending policies using Simplicity.

### Example Commands for Claude:
- *"Affiche ma politique de gouvernance actuelle."*
- *"Le membre X est parti. Voici le script de mise à jour pour le supprimer et son hash. Effectue la migration de politique."*

### Tools Exposed:
- `get_governance_policy()`: Fetches the current policy hash and version.
- `propose_policy_update(new_hash, proof)`: Submits a formally-verified Simplicity upgrade to the TEE.

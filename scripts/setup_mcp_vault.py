"""
One-time setup: create a vault + static_bearer credential that authenticates
this bot's own Managed Agent to its self-hosted /mcp endpoint.

Run once per environment (throwaway test agent, or production). Not part of
the request-handling path — main.py never imports this. Safe to re-run: it
reuses an existing vault with the same display name instead of creating a
duplicate.

Usage:
    MCP_SHARED_SECRET=... AGENT_ID=... ANTHROPIC_API_KEY=... python3 scripts/setup_mcp_vault.py
    MCP_SHARED_SECRET=... AGENT_ID=... ANTHROPIC_API_KEY=... python3 scripts/setup_mcp_vault.py --dry-run

IMPORTANT: on Railway, AGENT_ID lives in the service's environment variables,
not the local .env (the container filesystem is ephemeral — see CLAUDE.md).
Pull it from Railway before running this against production, not from a
local checkout that may be stale.

Prints MCP_VAULT_ID to paste into that environment's .env / Railway variables.
"""
import os
import sys

from anthropic import Anthropic

DRY_RUN = "--dry-run" in sys.argv

client = Anthropic()

agent_id = os.environ.get("AGENT_ID")
shared_secret = os.environ.get("MCP_SHARED_SECRET")

if not agent_id:
    sys.exit("AGENT_ID is not set — this script authenticates an existing agent's declared MCP server.")
if not shared_secret and not DRY_RUN:
    sys.exit("MCP_SHARED_SECRET is not set — generate one first, e.g. `python3 -c \"import secrets; print(secrets.token_hex(32))\"`.")

agent = client.beta.agents.retrieve(agent_id)

if not agent.mcp_servers:
    sys.exit(f"Agent {agent_id} ({agent.name!r}) has no declared mcp_servers — nothing to authenticate.")

print(f"Agent {agent_id} ({agent.name!r}) declares {len(agent.mcp_servers)} MCP server(s):")
for server in agent.mcp_servers:
    print(f"  - name={server.name!r} url={server.url!r}")

if len(agent.mcp_servers) > 1:
    sys.exit("More than one MCP server declared — edit this script to pick the right one explicitly.")

mcp_server_url = agent.mcp_servers[0].url

if DRY_RUN:
    print(f"\n[--dry-run] Would create/reuse a static_bearer credential keyed to: {mcp_server_url!r}")
    sys.exit(0)

# Confirm we're pointed at the agent the operator thinks we are — AGENT_ID can
# come from a stale local .env that no longer matches Railway's.
try:
    typed = input(f"\nType the agent ID ({agent_id}) to confirm this is the right agent: ")
except EOFError:
    sys.exit("No input available to confirm agent identity (running non-interactively?) — aborting.")
if typed.strip() != agent_id:
    sys.exit("Typed ID did not match — aborting.")

VAULT_NAME = f"{agent.name} — MCP auth"

existing_vault = None
for vault in client.beta.vaults.list():
    if vault.display_name == VAULT_NAME:
        existing_vault = vault
        break

if existing_vault is not None:
    print(f"Reusing existing vault: {existing_vault.id} (display_name={VAULT_NAME!r})")
    vault = existing_vault
    existing_credential = None
    for credential in client.beta.vaults.credentials.list(vault_id=vault.id):
        if getattr(credential.auth, "mcp_server_url", None) == mcp_server_url:
            existing_credential = credential
            break
    if existing_credential is not None:
        print(f"A credential for this exact URL already exists: {existing_credential.id}")
        print("Not creating another — archive it first if the secret needs rotating, then re-run.")
        print(f"\nMCP_VAULT_ID={vault.id}")
        sys.exit(0)
else:
    confirm = input(f"\nCreate a new vault + static_bearer credential keyed to exactly this URL: {mcp_server_url!r} ? [y/N] ")
    if confirm.strip().lower() != "y":
        sys.exit("Aborted.")
    vault = client.beta.vaults.create(display_name=VAULT_NAME)
    print(f"Created vault: {vault.id}")

credential = client.beta.vaults.credentials.create(
    vault_id=vault.id,
    display_name="Self-hosted MCP shared secret",
    auth={
        "type": "static_bearer",
        "mcp_server_url": mcp_server_url,
        "token": shared_secret,
    },
)
print(f"Created credential: {credential.id}")

print(f"\nMCP_VAULT_ID={vault.id}")
print("Paste this into the environment's .env / Railway variables, alongside the same MCP_SHARED_SECRET used above.")

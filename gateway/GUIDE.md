# Personal Secure MCP Tunnel

This is the default ChatGPT path for one person who wants to keep Sense, Corpus, and Hypes on a
personal Mac. It uses the product plugins already installed and enabled locally. It does not
reinstall them, create a second product data root, copy a database, or turn the gateway into a
fourth plugin.

The gateway is optional toolkit infrastructure. Local-only users install the product plugins and
stop there. ChatGPT users add this gateway, one OpenAI Secure MCP Tunnel per selected product, and
one developer connection per selected product. OpenAI's current transport contract is in the
[Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

## One gateway, selected independent products

Each selected product keeps a fixed path and its own ChatGPT registration:

```text
installed Sense launcher  -> 127.0.0.1:18181/mcp --\
installed Corpus launcher -> 127.0.0.1:18182/mcp --- one fixed-path gateway :18180
installed Hypes launcher  -> 127.0.0.1:18183/mcp --/       |       |       |
                                                         Sense   Corpus  Hypes tunnels
```

The public gateway paths are still `/sense/mcp`, `/corpus/mcp`, and `/hypes/mcp`. The gateway does
not create a combined MCP surface or let a model route between products. Startup verifies that
each installed launcher exposes exactly its reviewed product tools. A missing, disabled, changed,
or cross-wired product fails closed.

You may select any non-empty subset. Sense alone needs one Platform tunnel and one developer
connection; Sense plus Hypes needs two; all three need three. The number of LaunchAgents stays one.
That single supervisor starts the selected installed product servers, the gateway, and the selected
tunnel-client processes. A child failure restarts the unit, so selected Chat connections share an
availability boundary while product tools and data remain separate.

The gateway listens only on loopback. The official tunnel client makes an outbound connection to
OpenAI; no inbound router port, public domain, hosted OAuth provider, or AWS service is required.
The API key is available only to tunnel-client children. Product servers and the gateway do not
receive it.

## Prepare selected tunnels

Create a distinct Platform tunnel for every product you want in ChatGPT. Associate each tunnel with
the same personal Platform organization and ChatGPT workspace. Keep the runtime API key out of
commands, profiles, plugin packages, and the repository.

Generate the selected profiles. Omit products you do not want:

```text
./gateway/launchers/personal-agent-tunnel \
  --sense-tunnel-id tunnel_<sense-id> \
  --hypes-tunnel-id tunnel_<hypes-id> \
  --connection-mode gateway \
  --gateway-base-url http://127.0.0.1:18180 \
  --format shell
```

The planner prints only the official client's `init`, `doctor`, and `run` commands. It never reads
or prints the API key. The `personal-agent-gateway-*` profiles are separate from the older direct
`personal-agent-*` profiles, which preserves a reversible fallback. Do not run both profiles for
the same tunnel id at once.

Run the generated `init` commands, then run each `doctor` command. Store the runtime API key in the
login Keychain under service `personal-agent-tunnel-control-plane`, account `user`; use Keychain
Access or another prompt-safe method rather than putting it in shell history.

## Install one macOS service

The ordinary path discovers enabled `sense`, `corpus`, and `hypes` entries from the local
`personal-agent-toolkit` Codex marketplace. Select only products that are already installed:

```text
GATEWAY_ROOT=/absolute/path/to/personal-agent-toolkit/gateway
TUNNEL_CLIENT=/absolute/path/to/tunnel-client

"$GATEWAY_ROOT/launchers/personal-agent-tunnel-service" \
  install-gateway-launch-agent \
  --runtime-program "$GATEWAY_ROOT/launchers/personal-agent-tunnel-service" \
  --gateway-program "$GATEWAY_ROOT/launchers/personal-agent-tunnel-gateway" \
  --tunnel-client "$TUNNEL_CLIENT" \
  --product sense \
  --product hypes
```

For an equivalent local package not registered in Codex, add one exact root for every selected
product, for example `--product-root sense=/absolute/path/to/sense`. The root must contain the
matching manifest, package version, and packaged launcher and must be owned and not group/other
writable. Manual roots are an alternative discovery route, not a product reinstall.

The command writes only
`~/Library/LaunchAgents/com.ruzzy77.personal-agent-tunnel.gateway.plist`. The plist stores selected
product roots, profile names, the verified `uv` executable path needed by launchd's restricted
environment, and the Keychain item identity; it stores no key.
Load or restart that one LaunchAgent, then check `http://127.0.0.1:18180/healthz`. Its response names
the selected products and reports `product_runtime: installed-plugin`. Routine child output is
discarded; bounded errors go to `~/Library/Logs/PersonalAgentTunnel/gateway.stderr.log`.

## Connect ChatGPT and package only what you use

In ChatGPT developer settings, create one app per selected product, choose Tunnel, and select that
product's tunnel. Verify the discovered tools before continuing:

- Sense exposes Sense tools only.
- Corpus exposes Corpus tools only.
- Hypes exposes Hypes tools only.

Copy each created connection's `plugin_asdk_app_...` technical id from its browser URL. Build one
marketplace containing only those independently installable product plugins:

```text
uv --project owners/remote-runtime run python \
  owners/remote-runtime/plugin_release/build.py \
  --connection-mode tunnel \
  --sense-app-id plugin_asdk_app_<sense> \
  --hypes-app-id plugin_asdk_app_<hypes> \
  --plugin-validator /path/to/plugin-creator/scripts/validate_plugin.py
```

Tunnel mode copies each selected product's existing local-tool skill and its app binding. It does
not copy the local MCP launcher into the Chat package and does not add a gateway plugin. First test
each raw developer connection in a fresh Chat. Test full skill-plus-app packaging separately on a
ChatGPT surface that supports local marketplace plugins.

A raw developer connection receives the MCP server instructions, tool descriptions, parameter
schemas, and tool annotations, but not the product skill bundle. The three products therefore keep
their basic routing boundaries in MCP metadata: when the product is relevant, when it should not be
called, which read should precede a write, and when a destructive preview is required. This makes a
raw connection usable and safer, but it does not reproduce a skill's full response composition,
teaching behavior, or multi-call workflow. Treat the raw-connection check and the full plugin check
as different evidence.

## Switch from local-only use, update, and roll back

Enabling the gateway does not replace local Codex or Claude installation. Local tasks continue to
call the installed product plugin normally; Chat calls the same package launcher through its fixed
gateway path. The product's existing data directories and Corpus source registrations remain in
place.

After a product plugin update, rerun `install-gateway-launch-agent` and restart the one LaunchAgent.
This refreshes the recorded installed roots and reviewed tool surface without migrating data or
recreating tunnel or app ids. A changed tool set must be reviewed and released before startup will
accept it.

To return to local-only use, unload the gateway LaunchAgent and stop its tunnel profiles. Do not
remove the product plugins or their data. The local plugins remain available; only Chat developer
connections become offline. The older direct stdio tunnel mode remains a diagnostic fallback, not a
second simultaneous host.

## Move the personal host

Use only one active host per product tunnel. To move to an iMac or Synology:

1. stop the old host's single gateway service;
2. install and validate the selected product versions on the new host;
3. deliberately move or recreate product data and Corpus source registrations;
4. generate the same selected gateway profiles with the same tunnel ids;
5. install the one gateway service and verify each product in a fresh Chat.

An iMac is the lower-risk permanent host because it preserves the current macOS launchers and file
processing. A Synology needs a separate container, architecture, permissions, SQLite/WAL,
file-locking, and Corpus extraction check. Never put a live product SQLite root on NFS or let two
hosts write the same copied state concurrently.

Secure MCP Tunnel is a private transport, not a public plugin submission. Another person can use
the same public toolkit with their own selected products, tunnels, developer connections, device,
and data. Their tunnel and app ids do not belong in the shared source package.

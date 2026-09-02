# Personal Agent Context service

Owner-operated remote MCP and synchronization service for Sense, Corpus, and
Hypes.

The service uses the existing Personal Agent Auth Worker for OAuth, D1 for
owner-level state, one SQLite-backed Durable Object per Corpus Source, and a
SyncBroker Durable Object for outbound macOS Sync connections.

## Local checks

```sh
npm install
npm run check
```

Copy `wrangler.example.jsonc` to the ignored `wrangler.jsonc`, provision the D1
database and Durable Object namespaces, and set the resource URLs to their
final HTTPS endpoints. Credentials and production resource identifiers do not
belong in the repository.

## Endpoints

- `/sense/mcp`
- `/corpus/mcp`
- `/hypes/mcp`
- `/sync/v1/connect`
- `/health`

The Sync endpoint accepts only the dedicated device credential. MCP endpoints
accept resource-specific owner OAuth tokens. Local absolute paths are never
returned or stored by the service.


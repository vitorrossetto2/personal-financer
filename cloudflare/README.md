# Publish personal-financer to Cloudflare Workers + D1

This deployment uses a Python Worker running the existing Flask app and the `DB`
D1 binding. It targets your existing Worker and database, both named
`personal-financer`. Local `uv run python run.py` and its SQLite/MCP integration
continue to work independently.

## 1. Prepare the build

Install Node.js 22+ and uv. From the repository root:

```powershell
uv sync --project cloudflare --link-mode copy
uv run --project cloudflare python scripts/prepare_cloudflare.py
cd cloudflare
npm ci
```

The Worker dependencies are isolated from the local MCP and Authlib dependencies.
The build copies only application Python files, templates, and static assets.
It does not package `.env`, `instance/financeiro.db`, exports, tests, or MCP.
Wrangler also runs the preparation script on subsequent dev/deploy commands.

On Windows, if your filesystem rejects hardlinks, set this for the current shell:

```powershell
$env:UV_LINK_MODE = "copy"
```

The project installs its own recent uv for pywrangler; no global uv upgrade is required.

## 2. Connect the existing D1 database

All commands below run inside `cloudflare/` unless stated otherwise.

```powershell
uv run pywrangler login
uv run pywrangler d1 info personal-financer
```

Copy the existing database UUID into `database_id` in `wrangler.jsonc`, replacing
`REPLACE_WITH_EXISTING_D1_DATABASE_ID`. Keep `binding` as `DB` and both resource
names as `personal-financer`. Do not create another database or Worker.

## 3. Initialize D1

For a new, empty database with no existing local data to transfer:

```powershell
uv run pywrangler d1 migrations apply personal-financer --remote
```

This creates the schema and the two default bank accounts with zero balances.
Schema changes run through migrations, not on every web request.

### Optional: transfer your existing local SQLite data

Do this **instead of step 3 above**, before the first remote migration, and only
when the target D1 database is empty. The exporter reads a snapshot and leaves the
local database unchanged. It includes balances, cards, recurrences, transactions,
import keys, projections, and soft-deleted entries. The export contains private
financial data and is written under the Git-ignored `instance/` directory.

From the repository root:

```powershell
uv run --project cloudflare python scripts/export_d1.py
cd cloudflare
uv run pywrangler d1 execute personal-financer --remote --file ../instance/d1-export.sql
uv run pywrangler d1 migrations apply personal-financer --remote
```

Do not import this export into an already initialized or populated D1 database;
it is a one-time transfer, not a merge. If you already initialized D1, use a
separate, reviewed data migration. An existing SQL export will not be overwritten.

## 4. Configure Google login and session secrets

Set these secrets on the existing Worker (the CLI prompts for each value):

```powershell
uv run pywrangler secret put GOOGLE_CLIENT_ID
uv run pywrangler secret put GOOGLE_CLIENT_SECRET
uv run pywrangler secret put NOVOSALDO_ALLOWED_EMAIL
uv run pywrangler secret put NOVOSALDO_SECRET_KEY
```

Use the Google Web application OAuth client. Generate a persistent session key:

```powershell
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

In Google Cloud, add the exact production redirect URI:

```text
https://personal-financer.<your-workers-subdomain>.workers.dev/auth/google/callback
```

If using a custom domain, register its exact HTTPS callback instead. Only the
verified Google email in `NOVOSALDO_ALLOWED_EMAIL` can sign in. The Worker uses
Fetch for token exchange and UserInfo, with OAuth state and PKCE verification.
It never exposes the local MCP server or enables the local authentication bypass.
Production HTTPS responses use secure session cookies. Missing session secrets
produce a configuration error rather than a random, unstable session key.

## 5. Check and deploy

```powershell
uv run pywrangler deploy --dry-run
uv run pywrangler deploy
```

The second command publishes to your existing `personal-financer` Worker.
Rebuild/deploy after changing application files in the repository root.

For Cloudflare Workers Builds with the repository root as its root directory:

- Build command: `uv sync --project cloudflare && uv run --project cloudflare python scripts/prepare_cloudflare.py && npm ci --prefix cloudflare`
- Deploy command: `cd cloudflare && uv run pywrangler deploy`

Commit the real database UUID before enabling automatic deployment. Apply new D1
migrations explicitly before deploying code that needs them.

## Local Worker development

Copy `.dev.vars.example` to `.dev.vars` inside this folder and fill in test/dev
Google credentials plus a session secret. Register this Google callback:
`http://localhost:8787/auth/google/callback`.

```powershell
uv run pywrangler d1 migrations apply personal-financer --local
uv run pywrangler dev
```

Open `http://localhost:8787`. Local D1 is separate from both remote D1 and the
original SQLite database. Google login is still required. The `.dev.vars` file
is ignored by Git and is not uploaded as production secrets.

## Validation and operational details

From the repository root, run `uv run python -m unittest discover -s tests -v`.
The tests cover existing accounting behavior, the D1 interface, atomic rollback,
import reconciliation, and Worker OAuth state/PKCE handling.

Installments and recurring entries use atomic D1 batches. Invoice imports load
only the selected card's rows into temporary in-memory SQLite to evaluate the
existing matching rules, then submit all writes as a single D1 batch. This
preserves future-installment confirmation and incremental imports without a
network round trip for every matching query. Temporary SQLite is never durable
storage. Concurrent conflicting inserts fail the batch and can be retried by
reimporting. Very large card histories/imports remain subject to Worker memory,
CPU, and D1 limits; batches are not split because that would allow partial imports.

Reference: [Cloudflare Flask support](https://developers.cloudflare.com/workers/languages/python/packages/flask/)
and [D1 batch API](https://developers.cloudflare.com/d1/worker-api/d1-database/).

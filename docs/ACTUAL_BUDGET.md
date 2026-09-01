# Actual Budget transaction sync

PiStats can import normalized, user-approved bank SMS transactions into a
self-hosted Actual Budget instance. The integration is disabled by default and
does not start, stop, or otherwise manage the Actual service.

Actual does not expose a general transaction-writing REST API. PiStats therefore
uses Actual's official [`@actual-app/api`](https://actualbudget.org/docs/api/)
Node package. Its local budget engine downloads a private cache, imports through
`importTransactions`, synchronizes the result, and shuts down cleanly. The API
package should match the version of the Actual server to avoid migration
incompatibilities.

## Privacy and import behavior

- Android parses the SMS locally and sends only normalized transaction fields.
- The backend rejects unknown JSON fields, including any attempted full-message
  field, and never stores the payee, amount, reference, or SMS body in its
  idempotency database.
- The exact normalized sender plus `account_hint` must match an administrator
  mapping. PiStats never guesses an Actual account.
- The event currency must exactly match the configured currency of the selected
  Actual budget. PiStats rejects mismatches before invoking the Actual bridge.
- `event_id` becomes Actual's `imported_id`, and a private SQLite database adds
  backend idempotency. A retry after a completed import returns HTTP `409`.
- Imports are serialized, marked uncleared, use `reimportDeleted: false`, and
  retain the original normalized payee capitalization.

Keep PiStats and Actual on localhost, a LAN, or a trusted private network. Do
not expose either service directly to the public internet.

## Prerequisites

You need:

- a running Actual Budget server;
- its login password;
- the budget Sync ID from Actual's advanced settings;
- Node.js 22 or newer, as required by current Actual tooling; and
- the version of `@actual-app/api` matching the Actual server.

For a Docker deployment, get the running server version without changing it:

```bash
docker inspect --format '{{.Config.Image}}' actual-budget
```

Container names and image tags are installation-specific. You can also read the
version in Actual's settings UI.

## Install the official Actual API client

Replace `26.8.1` with the version of your Actual server. The dedicated prefix
keeps this optional dependency out of the PiStats source tree and root-owned so
the unprivileged service cannot modify executable code:

```bash
sudo install -d -o root -g root -m 0755 /usr/local/lib/pistats-actual-api
sudo npm install --omit=dev --prefix /usr/local/lib/pistats-actual-api \
  @actual-app/api@26.8.1
sudo chmod -R go-w /usr/local/lib/pistats-actual-api
```

The Debian package does not download npm content during installation. This
keeps the base PiStats package lightweight and lets each administrator install
the API version compatible with their own Actual server.

## Create exact account mappings

Create `/etc/pistats/actual-account-mappings.json`:

```json
{
  "mappings": [
    {
      "sender": "VM-EXAMPLE",
      "account_hint": "1234",
      "actual_account_id": "replace-with-an-actual-account-id"
    }
  ]
}
```

Add one entry for every sender/account combination that may be approved in the
app. Sender matching is case-insensitive after trimming; account hints remain
exact after trimming and uppercasing. Use JSON `null` only for a sender whose
messages genuinely contain no account hint. Duplicate normalized mappings make
the backend fail at startup.

The account ID is the stable ID shown in Actual account URLs and returned by
Actual's API—not the account display name. Restrict the file to root and the
PiStats service group:

```bash
sudo chown root:pistats /etc/pistats/actual-account-mappings.json
sudo chmod 0640 /etc/pistats/actual-account-mappings.json
```

For a source installation, replace group `pistats` with the configured service
user's primary group and choose a private path readable by that user.

## Configure PiStats

Edit `/etc/pistats/pistats.env` for an APT installation:

```dotenv
PISTATS_ACTUAL_SERVER_URL=http://127.0.0.1:5006
PISTATS_ACTUAL_PASSWORD=replace-with-the-actual-server-password
PISTATS_ACTUAL_SYNC_ID=replace-with-the-budget-sync-id
PISTATS_ACTUAL_CURRENCY=INR
PISTATS_ACTUAL_MAPPINGS_FILE=/etc/pistats/actual-account-mappings.json
PISTATS_ACTUAL_API_MODULE=/usr/local/lib/pistats-actual-api/node_modules/@actual-app/api
PISTATS_ACTUAL_DATA_DIR=/var/lib/pistats/actual-cache
PISTATS_TRANSACTION_DATABASE=/var/lib/pistats/transactions.sqlite3
```

If the budget has end-to-end encryption, also set:

```dotenv
PISTATS_ACTUAL_ENCRYPTION_PASSWORD=replace-with-the-budget-encryption-password
```

`PISTATS_ACTUAL_SERVER_URL` may be any administrator-selected HTTP or HTTPS URL
without embedded credentials, a query, or a fragment. For a self-signed HTTPS
server, set `NODE_EXTRA_CA_CERTS` in the service environment to a trusted CA
certificate. Disabling TLS verification is not supported.

`PISTATS_ACTUAL_CURRENCY` must be the three-letter currency code configured for
the selected budget, such as `INR`. PiStats does not perform currency conversion;
an event with any other currency returns `422 transaction_currency_mismatch`.

Password values are preserved exactly. If a server or encryption password starts
or ends with whitespace, quote the entire value in the systemd environment file,
for example `PISTATS_ACTUAL_PASSWORD=" password with edge spaces "`.

The five core settings—server URL, password, sync ID, currency, and mappings
file—must be provided together. Partial configuration stops PiStats at startup
instead of silently exposing a broken feature.

Apply the configuration:

```bash
sudo systemctl restart pistats-backend
sudo systemctl status pistats-backend --no-pager
```

## Verify

Load the PiStats token without printing it and check capability discovery:

```bash
PISTATS_TOKEN="$(sudo sed -n 's/^PISTATS_TOKEN=//p' /etc/pistats/pistats.env)"
curl -H "Authorization: Bearer ${PISTATS_TOKEN}" \
  http://127.0.0.1:8787/api/health
```

The response reports:

```json
{
  "features": {
    "transaction_sync": true,
    "actual_budget": true
  }
}
```

`transaction_sync` means the intake code is installed. `actual_budget` is a
cached runtime check and becomes false when Node, the API module, Actual, the
password, the budget, encryption, or any mapped account cannot be validated.
PiStats retries the check after `PISTATS_ACTUAL_HEALTH_CACHE_SECONDS` (30 seconds
by default).

View errors without exposing request bodies or credentials:

```bash
sudo journalctl -u pistats-backend -n 100 --no-pager
```

Common results:

- `403 transaction_sync_not_configured`: the five core settings are absent.
- `422 account_mapping_not_found`: the sender/account hint has no exact mapping,
  or the mapped Actual account no longer exists.
- `422 transaction_currency_mismatch`: the SMS currency differs from
  `PISTATS_ACTUAL_CURRENCY`; no Actual import was attempted.
- `502 actual_import_failed`: Actual or its API client failed; the Android app
  can safely retry using the same idempotency key.
- `409 already_imported`: this event was already completed and is successful
  from the client's perspective.

## Disable or remove

Remove the five core `PISTATS_ACTUAL_*` settings and restart PiStats. The intake
endpoint remains installed, but `actual_budget` becomes false and imports return
`403`. PiStats does not stop or modify the Actual service.

The local cache and idempotency database are retained intentionally. Remove
them only after confirming no pending retry or audit state is needed.

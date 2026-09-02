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
- The normalized sender must contain an administrator-configured bank fragment,
  while `account_hint` must match exactly. PiStats rejects ambiguous matches
  instead of guessing an Actual account.
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

## Create account mappings

Create `/etc/pistats/actual-account-mappings.json`:

```json
{
  "mappings": [
    {
      "label": "Example Bank Savings •1234",
      "sender": "EXAMPLE",
      "account_hint": "1234",
      "actual_account_id": "replace-with-an-actual-account-id"
    }
  ]
}
```

Add one entry for every bank identifier/account combination that may be approved
in the app. `label` is the user-facing bank-account name shown in Android's
review selector; it must not contain credentials or other secrets. It is
optional for compatibility with older mapping files, in which case PiStats
generates a label from the sender and account hint. The configured `sender` is a case-insensitive substring, so a value
such as `HDFCBK` matches senders including `VM-HDFCBK` and `AD-HDFCBK`. Use a
distinctive bank identifier rather than a generic fragment. `account_hint` is
required, must be a non-empty string, and remains exact after trimming and
uppercasing. Mappings with a missing or `null` hint make the backend fail at
startup. Duplicate normalized mappings also fail at startup. If one event
matches sender fragments mapped to different Actual accounts, the request is
rejected as ambiguous.

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

Verify the safe account choices exposed to Android. Actual account IDs are
intentionally omitted:

```bash
curl -H "Authorization: Bearer ${PISTATS_TOKEN}" \
  http://127.0.0.1:8787/api/transactions/accounts
```

View errors without exposing request bodies or credentials:

```bash
sudo journalctl -u pistats-backend -n 100 --no-pager
```

### Diagnose one failed app sync

The Android error card shows a copyable **Diagnostic ID** for every attempted
upload. Search the Pi journal with that UUID:

```bash
DIAGNOSTIC_ID='paste-the-uuid-from-the-app'
sudo journalctl -u pistats-backend --no-pager \
  --grep "request_id=${DIAGNOSTIC_ID}"
```

A result looks like this and contains no transaction data:

```text
transaction_sync request_id=d5675780-a6ad-4e3d-b1f6-35c6703bc123 outcome=failed code=network
```

Interpret `outcome` as follows:

- `imported`: Actual accepted the transaction.
- `already_imported`: the idempotency key had completed earlier; this is success.
- `rejected`: PiStats rejected authentication, validation, currency, or mapping.
- `failed`: PiStats state storage or the Actual bridge failed; the error code
  identifies the safe failure category.

If journald retention has expired, query the persistent import record. Use the
path configured by `PISTATS_TRANSACTION_DATABASE`; the Debian package default is
shown here:

```bash
sudo sqlite3 /var/lib/pistats/transactions.sqlite3 \
  "SELECT request_id, state, error_code, datetime(updated_at, 'unixepoch', 'localtime') FROM transaction_imports WHERE request_id = '${DIAGNOSTIC_ID}';"
```

An empty database result means the request was rejected before import state
could be created, never reached the Pi, or has a different configured database
path. `client_timeout`, `client_io_error`, and `unexpected_client_error` are
app-side codes, so they may have no matching Pi entry. For those, check network
reachability and Android logs:

```bash
adb logcat -s PiStatsTransactionSync:I
```

Share the diagnostic ID and error code for support. Do not share the raw SMS,
bearer token, Actual password, mapping file, or database.

Common results:

- `403 transaction_sync_not_configured`: the five core settings are absent.
- `422 account_mapping_not_found`: no sender fragment and exact account hint
  combination matches, or the mapped Actual account no longer exists.
- `422 account_mapping_ambiguous`: multiple sender fragments match the same
  event and point to different Actual accounts.
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

# Transaction sync API

PiStats can capture future bank transaction SMS messages on Android, parse them locally, and send approved normalized events to the private Pi backend. The Android app never sends or persists the complete SMS body.

## Capability discovery

The backend advertises the feature from `GET /api/health`:

```json
{
  "status": "ok",
  "features": {
    "transaction_sync": true,
    "actual_budget": true
  }
}
```

`transaction_sync` means the PiStats intake endpoint is installed. `actual_budget`
must reflect the current runtime health of the Actual Budget service, not merely
whether it is configured. The dashboard exposes transaction sync, and the app
allows its manual enable/disable switch, only while both values are true. Every
background upload re-checks both flags before sending. Demo mode exposes a safe
preview but never requests SMS access.

## Account options endpoint

`GET /api/transactions/accounts`

The authenticated endpoint returns the administrator-defined account choices
used by Android's transaction-review selector:

```json
{
  "accounts": [
    {
      "mapping_id": "0133fd38-b0e0-5fe8-a97d-7ffcbfc773a4",
      "label": "HDFC Savings •1234",
      "sender_contains": "HDFCBK",
      "account_hint": "1234"
    }
  ]
}
```

`mapping_id` is a stable opaque identifier derived from the complete private
mapping. The response never exposes Actual account IDs. Android fetches these
options before enabling capture and requires the user to select an account when
reviewing a transaction.

## Import endpoint

`POST /api/transactions/sms`

Headers:

- `Authorization: Bearer <PiStats token>`
- `Idempotency-Key: sms-v1:<sha256>`
- `X-PiStats-Device-Id: <installation UUID>`
- `X-PiStats-Request-Id: <attempt UUID>`
- `Content-Type: application/json`

Example body:

```json
{
  "event_id": "sms-v1:3e6...",
  "received_at_millis": 1788249600000,
  "transaction_date": "2026-09-01",
  "transaction_time": "14:30:00",
  "amount_minor": -24550,
  "currency": "INR",
  "direction": "debit",
  "payee": "SWIGGY",
  "account_mapping_id": "0133fd38-b0e0-5fe8-a97d-7ffcbfc773a4",
  "account_hint": "1234",
  "bank_reference": "123456789",
  "sender": "VM-HDFCBK",
  "source": "bank_sms",
  "cleared": false
}
```

Debits use a negative `amount_minor`; credits use a positive value. The backend
requires a configured three-letter currency for the selected Actual budget and
rejects an event whose `currency` differs before calling Actual. It maps a
case-insensitive sender substring plus an exact normalized `account_hint` to a
configured Actual Budget account and rejects unknown or ambiguous mappings
instead of guessing. Reviewed Android events also include `account_mapping_id`;
the backend verifies that the selected mapping still matches the sender and
account hint rather than trusting a client-supplied Actual account ID.
`account_hint` is required and must be a non-empty string. `bank_reference` is
optional and may be omitted or set to `null`.

Recommended responses:

- `201` or `204`: imported through `@actual-app/api.importTransactions`.
- `409`: the same idempotency key was already imported. The app treats this as success.
- `401`/`403`: invalid credentials or disabled capability.
- `404`: endpoint not installed.
- `422`: invalid event, currency mismatch, or missing/ambiguous account mapping.
- `5xx`: retryable backend/Actual failure.

Every response echoes a canonical `X-PiStats-Request-Id`. The backend writes
one privacy-safe journal entry for the attempt containing only that request ID,
an outcome, and a bounded error code. It never logs the SMS body, sender,
account, amount, currency, payee, reference, device ID, bearer token, or Actual
credentials. The same request ID is stored with the import state so an
administrator can correlate an app failure with the Pi without receiving the
transaction details.

The backend should use `event_id` as Actual's stable `imported_id`, import the transaction as uncleared, serialize writes to the budget, and avoid logging complete request bodies.

The optional `transaction_date` and `transaction_time` fields are the date and
time parsed from the bank message. The date must use `YYYY-MM-DD`, and the time
must use `HH:MM:SS`. When no transaction date is available, the backend derives
the date from `received_at_millis` in the server's local timezone.

The implementation accepts only the documented normalized fields, limits the
JSON body to 16 KiB, requires a canonical installation UUID, and requires the
idempotency header to equal `event_id`. This intentionally rejects any added
field that could carry the complete SMS body.

## Backend configuration

Transaction intake is present in every installation, so `transaction_sync` is
always `true`. Actual import remains opt-in. `actual_budget` becomes `true` only
after the official Actual API client can authenticate, download the configured
budget, and find every mapped account.

See [Actual Budget transaction sync](docs/ACTUAL_BUDGET.md) for setup and the
account-mapping file format.

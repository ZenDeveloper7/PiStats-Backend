# Media backup API

PiStats discovers media through Android `MediaStore` and streams each file to the
existing Pi service. The Pi service, not the Android app, owns filesystem and
optional network-share credentials.

## Endpoint

```http
POST /api/media/backup/items?display_name=IMG_0001.jpg&mime_type=image%2Fjpeg&size_bytes=1234&modified_at_seconds=1700000000&captured_at_millis=1699999999000&relative_path=DCIM%2FCamera%2F
Authorization: Bearer <existing PiStats token>
Idempotency-Key: <device UUID>:external:<MediaStore ID>:<modified seconds>
X-PiStats-Device-Id: <app-generated UUID>
X-PiStats-Media-Key: external:<MediaStore ID>:<modified seconds>
Content-Type: image/jpeg
Content-Length: 1234

<raw file bytes>
```

`captured_at_millis` and `relative_path` are optional. The client does not send
latitude, longitude, or a device hardware identifier.

## Responses

| Status | Meaning |
|---|---|
| `200`, `201`, `202`, `204` | Stored successfully |
| `409` | This idempotency key was already stored; treated as success |
| `401`, `403` | Authentication or authorization problem |
| `404` | Media backup is not installed on the Pi service |
| `413` | File is skipped and the scan continues |
| `408`, `5xx` | Temporary failure; WorkManager retries with backoff |
| Other `4xx` | Permanent request failure shown in the app |

## Required server behavior

1. Authenticate before reading the request body.
2. Enforce a configured maximum content length and allow only intended image and
   video MIME types.
3. Ignore path separators from `display_name`; generate the destination path on
   the server. Treat `relative_path` as untrusted display metadata, not a path.
4. Stream to a temporary file on the same filesystem, verify the received byte
   count, `fsync`, and atomically rename it into the configured library.
5. Store the idempotency key in a durable database with a unique constraint. A
   repeated completed request returns `409` without creating a second file.
6. Keep incomplete temporary files outside the shared library and remove them on
   a schedule.
7. Never expose filesystem or network-share credentials to the Android client.
   Bind privately and use the installation's private-network access controls.

One practical destination layout is:

```text
<media-root>/<device-id>/<yyyy>/<MM>/<server-generated-name>
```

The server should derive the date from `captured_at_millis`, falling back to
`modified_at_seconds`, and should preserve the original extension only after
validating it against the MIME type.

## Client scheduling behavior

- Automatic work runs every six hours with battery-not-low and network
  constraints.
- Wi-Fi-only mode uses WorkManager's `UNMETERED` network constraint.
- Each worker processes at most 20 files and schedules another constrained batch
  when more remain. This keeps work restartable and avoids a long-running
  foreground service.
- The scan checkpoint advances after a success, `409`, or an explicitly skipped
  missing/oversized file. Resetting the scan is safe because the server contract
  is idempotent.

Large single videos still need to finish within the request timeout. A future
server revision should add resumable, offset-based upload sessions before this is
used for very large video libraries.

## Pi configuration

Set `PISTATS_MEDIA_BACKUP_ROOT` to an existing, service-writable directory on the
desired filesystem to enable this endpoint. That directory may also be exported
by Samba or another file-sharing service, but PiStats does not require one. The
endpoint returns `404` when the value is unset. The following settings are optional:

- `PISTATS_MEDIA_BACKUP_MAX_BYTES` (default `1073741824`)
- `PISTATS_MEDIA_BACKUP_DATABASE` (defaults to a state directory beside the root)
- `PISTATS_MEDIA_BACKUP_TEMP_DIR` (defaults to that state directory's `tmp` folder)
- `PISTATS_MEDIA_BACKUP_TEMP_MAX_AGE_SECONDS` (default `86400`)
- `PISTATS_MEDIA_BACKUP_READ_TIMEOUT_SECONDS` (default `300`)

The temporary directory must be outside the shared library and on the same
filesystem. Startup fails if that invariant is not satisfied, avoiding
non-atomic cross-filesystem moves or exposing incomplete files.
The server removes stale partial uploads periodically even when no new uploads
arrive. Default state paths are isolated by the canonical media root, so moving
to a sibling library does not reuse completed idempotency records.

'use strict';

const RESULT_PREFIX = 'PISTATS_RESULT:';

function result(payload) {
  process.stdout.write(`${RESULT_PREFIX}${JSON.stringify(payload)}\n`);
}

function requiredEnvironment(name) {
  const value = process.env[name];
  if (!value) {
    throw Object.assign(new Error(`Missing ${name}`), { code: 'bridge_not_configured' });
  }
  return value;
}

function safeErrorCode(error) {
  const allowed = new Set([
    'network-failure',
    'network',
    'invalid-password',
    'token-expired',
    'unauthorized',
    'budget-not-found',
    'missing-key',
    'decrypt-failure',
    'old-key-style',
    'out-of-sync-migrations',
    'account_not_found',
    'actual_import_rejected',
    'bridge_not_configured',
    'invalid_bridge_request',
  ]);
  return error && typeof error.code === 'string' && allowed.has(error.code)
    ? error.code
    : 'actual_api_error';
}

async function readRequest() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > 65536) {
      throw Object.assign(new Error('Request too large'), { code: 'invalid_bridge_request' });
    }
    chunks.push(chunk);
  }
  const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!payload || typeof payload !== 'object' || !['check', 'import'].includes(payload.action)) {
    throw Object.assign(new Error('Invalid request'), { code: 'invalid_bridge_request' });
  }
  return payload;
}

async function main() {
  const request = await readRequest();
  const moduleName = process.env.PISTATS_ACTUAL_API_MODULE || '@actual-app/api';
  const api = require(moduleName);
  const encryptionPassword = process.env.PISTATS_ACTUAL_ENCRYPTION_PASSWORD;
  let initialized = false;
  try {
    await api.init({
      dataDir: requiredEnvironment('PISTATS_ACTUAL_DATA_DIR'),
      serverURL: requiredEnvironment('PISTATS_ACTUAL_SERVER_URL'),
      password: requiredEnvironment('PISTATS_ACTUAL_PASSWORD'),
      verbose: false,
    });
    initialized = true;
    const downloadOptions = encryptionPassword ? { password: encryptionPassword } : undefined;
    await api.downloadBudget(requiredEnvironment('PISTATS_ACTUAL_SYNC_ID'), downloadOptions);
    const accounts = await api.getAccounts();
    const accountIds = new Set(accounts.map(account => account.id));

    if (request.action === 'check') {
      if (!Array.isArray(request.account_ids) || request.account_ids.some(id => !accountIds.has(id))) {
        throw Object.assign(new Error('Mapped account not found'), { code: 'account_not_found' });
      }
      result({ ok: true });
      return;
    }

    if (
      typeof request.account_id !== 'string' ||
      !accountIds.has(request.account_id) ||
      !request.transaction ||
      typeof request.transaction !== 'object'
    ) {
      throw Object.assign(new Error('Mapped account not found'), { code: 'account_not_found' });
    }
    const importResult = await api.importTransactions(
      request.account_id,
      [request.transaction],
      {
        defaultCleared: false,
        reimportDeleted: false,
        payeeNameNormalization: 'original',
      },
    );
    if (Array.isArray(importResult.errors) && importResult.errors.length > 0) {
      throw Object.assign(new Error('Actual rejected the import'), {
        code: 'actual_import_rejected',
      });
    }
    await api.sync();
    result({ ok: true });
  } finally {
    if (initialized) {
      await api.shutdown().catch(() => {});
    }
  }
}

main().catch(error => {
  result({ ok: false, code: safeErrorCode(error) });
  process.exitCode = 2;
});

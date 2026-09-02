# Contributing to OwnNode Agent

Thanks for helping improve OwnNode Agent. Focused fixes, new collectors,
documentation improvements, packaging work, and tests are welcome.

## Project rules

- Keep defaults generic and opt-in. Never add personal service names, hosts,
  MAC addresses, storage paths, account IDs, or credentials.
- Preserve bearer authentication and private-network defaults.
- Treat media and transaction data as sensitive. Tests and examples must use
  synthetic values.
- Coordinate Android contract changes with the
  [OwnNode app](https://github.com/ZenDeveloper7/OwnNode).
- Open an issue before a large API, persistence, or packaging change.

## Verify a change

Run the test suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Packaging changes should also build and pass lintian on Debian or in the
provided container:

```bash
./packaging/build-deb.sh
lintian --profile debian ../pistats-backend_*_amd64.changes
```

## Pull requests

- Explain the user-visible behavior and security impact.
- Add regression coverage for fixes.
- Update the API, installation, or release documentation when applicable.
- Run `git diff --check` and inspect the final diff for secrets and private data.

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).

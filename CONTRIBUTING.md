# Contributing

Thanks for helping improve CurbCharge. Start with the [development guide](docs/development.md)
for environment setup, test and formatting commands, and release workflow. The
[user guide](docs/usage.md) describes integration behavior and privacy.

## Contribution checklist

- Keep changes focused, work from `main`, and open one pull request per issue.
  Link the issue and explain the user-visible change and validation performed.
- Add or update tests for behavior changes. Shared fixtures are in
  [`tests/fixtures/`](tests/fixtures/) and are loaded by
  [`tests/conftest.py`](tests/conftest.py). Keep examples minimal, sanitized,
  and free of credentials or private provider data.
- Update user-facing strings in
  [`custom_components/curbcharge/strings.json`](custom_components/curbcharge/strings.json)
  and keep the supported files in
  [`custom_components/curbcharge/translations/`](custom_components/curbcharge/translations/)
  aligned.
- Treat config-entry unique IDs, provider IDs, station IDs, entity unique IDs,
  and keys stored in config-entry data or options as compatibility interfaces.
  Do not change them casually. If a format must change, add a versioned config
  entry migration and tests that preserve existing installations.
- Use only data and API access you are authorized to share. Never commit tokens,
  credentials, exact home coordinates, personal information, or unredacted
  provider responses.
- For provider-specific implementation context, see the
  [provider development guide tracked in issue #45](https://github.com/matthijssn/ha_curbcharge/issues/45);
  this file intentionally does not duplicate that guide.

Follow the checks in the [development guide](docs/development.md) before
submitting. The pull request template lists the compatibility, privacy, and
validation details reviewers need.

See the [Code of Conduct](CODE_OF_CONDUCT.md) and [Security policy](SECURITY.md).

# Development and releases

## Development

The protected `main` branch requires the `test`, `hassfest`, and `hacs` check
contexts. The Hassfest and HACS workflows use distinct job contexts so each
validator can block merges independently.

The `test` workflow checks the minimum Home Assistant 2024.12.0 release with
Python 3.12, `pytest-homeassistant-custom-component` 0.13.190, and Core's
2024.12.0 package constraints. It pins `pycares==4.8.0` because newer resolver
versions start a shutdown-loop thread that this older test plugin reports as a
teardown leak. The current-version job runs Home Assistant 2026.10.4 on
Python 3.13; Dependabot keeps that pin reviewable.

Install the Python test tools:

```bash
pip install -r requirements-dev.txt
```

Run the Python checks:

```bash
pytest --cov=custom_components/curbcharge --cov-fail-under=96
ruff check .
ruff format --check .
mypy
```

CI also enforces coverage strictly above 95% for each Python module under
`custom_components/curbcharge`; the package-wide 96% threshold does not replace
this per-module check.

The frontend test uses Node.js separately:

```bash
node --test tests/curbcharge-card.test.mjs
```

## HACS release smoke checklist

Run this on a clean Home Assistant instance for a full published release:

- [ ] Install the previous full release from the HACS default catalog, restart,
  and verify the integration sets up with the expected entities.
- [ ] Upgrade to the target full release through HACS, restart, and verify the
  config entry, options, devices, and entity IDs are preserved without
  duplicates.
- [ ] Record the Home Assistant version, source and target release tags, date,
  result, and any relevant logs in the release PR or issue. Remove credentials,
  coordinates, and personal data before attaching evidence.

HACS and Hassfest CI validate repository layout and metadata; they do not
replace this install/upgrade smoke test.

## Preparing a release

Home Assistant and HACS require a concrete version in `manifest.json`; do not
replace it with a template placeholder. To prepare a release, run the
**Release version** workflow from `main` and enter the intended GitHub tag,
such as `v0.1.2`. The workflow validates the tag, updates the manifest on a
`release/vX.Y.Z` branch, and provides a compare link for a pull request.

Review and merge that PR before creating the GitHub release from the same tag
on `main`. When the release is published, the workflow verifies that the tag
matches the manifest version and points to a commit on `main`.

## Versioning and upgrade compatibility

CurbCharge release tags use `vMAJOR.MINOR.PATCH`; `manifest.json` stores the
version without the `v`. The [Release version workflow](../.github/workflows/release-version.yml)
validates stable tags before preparing a manifest update.

While the project is pre-1.0:

- A patch release (`0.y.z`) is for fixes and maintenance and should not
  intentionally break existing behavior or stored configuration.
- A minor release (`0.(y+1).0`) is for features and should be additive where
  possible. Because CurbCharge is still 0.x, SemVer does not promise a stable
  public API; a minor release may contain a breaking change. Call it out with
  the existing [`breaking-change` release category](../.github/release.yml),
  explain its impact, and provide upgrade steps.
- After 1.0, patch and minor releases are backward-compatible; reserve major
  version bumps for intentional breaking changes.

Treat config-entry data and options, provider IDs, and station, device, and
entity unique IDs as persisted interfaces. Do not rename them casually. A
stored-data format change requires a versioned config-entry migration and
tests using an existing entry; identity changes need an explicit compatibility
and migration plan. Include required user actions in both the pull request and
release notes.

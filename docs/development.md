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
Python 3.14.2; Dependabot keeps that pin reviewable. The current-version test
run exposes `DeprecationWarning` messages.

The supported development environments are Linux:

- Minimum: CPython 3.12, Home Assistant 2024.12.0, and the same pinned test
  plugin and `pycares` override used by CI:

```bash
python3.12 -m venv .venv-min
source .venv-min/bin/activate
python -m pip install --constraint https://raw.githubusercontent.com/home-assistant/core/2024.12.0/homeassistant/package_constraints.txt pytest-homeassistant-custom-component==0.13.190 pycares==4.8.0
python -m pytest
deactivate
```

- Current: CPython 3.14.2 and the pinned Home Assistant/tool versions:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest --cov=custom_components/curbcharge --cov-fail-under=96
python -m ruff check .
python -m ruff format --check .
python -m mypy
node --test tests/curbcharge-card.test.mjs
```

The minimum environment follows the CI constraints file and avoids installing
the current-development dependency set alongside the older Core release.
Dependabot reviews the exact current tool pins in `requirements-dev.txt`.
Native Windows pytest collection is not supported by the Home Assistant test
plugin because it imports the Unix-only `fcntl` module; run the HA suites in
Linux/CI. Ruff, MyPy, and Node tests can still be run separately on Windows.

CI also enforces coverage strictly above 95% for each Python module under
`custom_components/curbcharge`; the package-wide 96% threshold does not replace
this per-module check. The no-decrease comparison uses the current lifecycle
tests in its detached base worktree so both revisions are measured against the
current Home Assistant child-device registry API.

The frontend test uses Node.js separately:

```bash
node --test tests/curbcharge-card.test.mjs
```

## HACS release smoke checklist

Run this on a clean Home Assistant instance for a full published release:

- [ ] Install the previous full release from the HACS default catalog, restart,
  and verify the integration sets up with the expected entities.
- [ ] Confirm the GitHub Release is published, `curbcharge.zip` is attached,
  and the downloaded ZIP passes the package validator.
- [ ] Upgrade to the target full release through HACS, restart, and verify the
  config entry, options, devices, and entity IDs are preserved without
  duplicates. Verify the bundled Lovelace card still loads.
- [ ] Record the Home Assistant version, source and target release tags, date,
  result, and any relevant logs in the release PR or issue. Remove credentials,
  coordinates, and personal data before attaching evidence.

HACS and Hassfest CI validate repository layout and metadata; they do not
replace this install/upgrade smoke test.

## Release ZIP packaging

The release package is a stdlib-built `dist/curbcharge.zip`. Its entries are
relative to the integration root: `manifest.json`, `__init__.py`, other runtime
modules, and the `translations/`, `brand/`, and `www/` directories.
HACS extracts release assets directly into `custom_components/curbcharge/`;
do not include a wrapper or `custom_components/curbcharge/` prefix in the ZIP.
Prefix stripping applies to repository source archives, not release assets.
The package tests reproduce direct release-asset extraction and verify the
installed files. The committed `www/curbcharge-card.js` is already the runtime
asset; there is no frontend build step.

Brand images live in `custom_components/curbcharge/brand/` because Home
Assistant 2026.3 and later serve custom-integration icons from the integration
itself through `/api/brands/integration/curbcharge/icon.png`. `icon.png` must be
256 by 256 pixels and `icon@2x.png` 512 by 512; `test_release_package.py`
enforces both. That pair is the complete set: a missing logo falls back to the
icon, and the square CurbCharge mark needs no separate `logo.png` or `dark_`
variant. Do not open a pull request against `home-assistant/brands`; it
auto-closes custom-integration submissions. The HACS dashboard still reads the
old brands CDN and therefore shows a placeholder tile until Home Assistant
2026.11 ([hacs/integration#5171](https://github.com/hacs/integration/issues/5171)).

Build and validate locally:

```bash
python3 scripts/release_package.py build
python3 scripts/release_package.py validate dist/curbcharge.zip
python3 -m unittest discover -s tests -p test_release_package.py
```

For a release tag, include `--tag vX.Y.Z` in either command. Validation checks
the ZIP layout and file inventory, manifest/tag version, JSON, Python syntax,
translations, card asset, and development-file exclusions. The inventory and
sizes are printed for review. The same ZIP built and validated by the tag
workflow is uploaded as a workflow artifact and attached to its draft GitHub
Release; it is not rebuilt for publication.

## Preparing a release

Home Assistant and HACS require a concrete version in `manifest.json`; do not
replace it with a template placeholder. To prepare a release, run the
**Release version** workflow from `main` and enter the intended GitHub tag,
such as `v0.1.2`. The workflow validates the tag, updates the manifest on a
`release/vX.Y.Z` branch, and provides a compare link for a pull request.

Review and merge that PR before creating a stable tag on `main`. Pushing the tag
runs `verify_tag`, which checks its exact commit SHA, manifest version, main
ancestry, and builds/validates `curbcharge.zip`. It then stages the exact
validated ZIP on a draft GitHub Release. The normal `test`, `hassfest`, and
`hacs` workflows also run for that tag. Publish the draft only after
`verify_tag`, `test`, `hassfest`, and `hacs` pass. Publishing triggers the
post-release verifier, which downloads and validates the attached asset. The
existing manual version-preparation job remains separately write-enabled to
push its reviewed manifest branch; tag artifact creation uses read-only
permissions, and only the draft-release job needs write permission.
The draft-release step selects its repository through `GH_REPO`, so it can
promote the validated artifact without checking out or rebuilding the source.

The HACS ZIP settings are versioned with the selected release. Earlier source
releases remain unchanged and have no custom ZIP asset; the first release whose
`hacs.json` enables `zip_release` must include the matching `curbcharge.zip`
before it is published. Do not publish that release manually before the draft
asset is ready.

Before upgrading an installation, make a Home Assistant backup that includes
the configuration and `.storage`. If a published release causes a regression,
pause further promotion, document the affected version, and prepare a corrective
release with a new tag; never move or reuse a published tag. Restore the backup
if the previous installation state is needed, and do not downgrade across a
documented data migration unless the release notes confirm compatibility.
Published assets are immutable: do not replace `curbcharge.zip` on a public
stable release. Correct a bad release with a higher patch version.

### Recovering from the v0.6.0 ZIP layout error

The published v0.6.0 ZIP incorrectly nests the integration beneath
`curbcharge-release/custom_components/curbcharge/`. After extraction, Home
Assistant cannot find the integration-root manifest and may report
`Integration 'curbcharge' not found`.

Publish the corrected package in a new patch release (v0.6.1), following the
normal version-preparation PR, tag validation, and draft publication steps.
Do not replace the v0.6.0 asset or move its published tag. Back up Home Assistant,
then use HACS to upgrade to the corrective release (or redownload that version
if HACS already reports it installed), and restart Home Assistant. Do not delete
the CurbCharge config entry or edit `.storage`; packaging recovery does not
require recreating configuration. Verify the config entry, options, devices,
entity IDs, and bundled card using the smoke checklist above. Record the live
result separately; archive extraction tests cannot prove a real HACS upgrade.

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

## Provider architecture

`provider.py` defines provider errors and the normalized provider contract;
`models.py` holds immutable stations, connectors, tariffs, and discovery
settings. `api.py` implements NDW, while `belgium.py` and `nobil.py` keep
source-specific behavior in their own adapters. `coordinator.py` owns one
provider update cycle, and `sensor.py`/`binary_sensor.py` expose normalized
data. Follow a provider's published terms and limits, add synthetic fixtures
and offline tests under `tests/`, and do not add live credentials or private
payloads to tests, issues, or logs.

## Security and external tooling

Security fixes are supported for the latest stable CurbCharge release. Older
release backports are considered case by case; no response or backport timeline
is promised. Report privately using [SECURITY.md](../SECURITY.md).

GitHub Actions are pinned by commit SHA and Dependabot reviews updates weekly.
The pinned HACS validator currently pulls its official `main` container image,
and Hassfest uses an official image without a version digest. Accept this
upstream-controlled freshness tradeoff for now; action SHA pinning does not
make those container contents hermetic. Reassess if upstream publishes stable
digests or a concrete integrity concern arises.

## Optional validation decisions

| Candidate | Decision | Rationale |
|---|---|---|
| Official HA container smoke | Defer | Current HA fixtures cover lifecycle behavior; exact archive checks are being added first. Reassess when a stable, isolated container runner is available. |
| HA beta | Advisory only after stable smoke | Do not increase the supported minimum or gate normal feature work on beta. |
| Branch coverage | Selective measurement | Protect changed critical branches; do not introduce a higher global threshold. |
| Browser/mobile/accessibility automation | Defer | Owner checks for keyboard, theme, narrow layout, and locales remain pending; automate only a demonstrated recurring gap. |
| CI caching/concurrency | Defer | Measure equivalent full runs before optimizing; never weaken required checks or cancel release validation. |
| Dependency review, CodeQL, dead-code tooling | Defer | Existing Dependabot, repository security controls, and required checks remain; add tools only for demonstrated value. |
| Frontend build/lint tooling | Reject for now | The card is one directly served JavaScript file with no runtime dependencies; Node tests are sufficient. |

The next-release HACS install/upgrade and `nb`/`sv` UI smoke checks have not
been run in this development environment. The owner-run locale gate remains
pending in [issue #257](https://github.com/matthijssn/ha_curbcharge/issues/257);
CI, archive validation, and source inspection are not substitutes for that
evidence.

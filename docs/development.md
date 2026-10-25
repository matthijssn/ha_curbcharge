# Development and releases

## Development

The protected `main` branch requires the `test`, `hassfest`, and `hacs` check
contexts. The Hassfest and HACS workflows use distinct job contexts so each
validator can block merges independently.

The `test` workflow checks the minimum Home Assistant 2024.12.0 release with
Python 3.12, `pytest-homeassistant-custom-component` 0.13.190, and Core's
2024.12.0 package constraints. It then runs the current Home Assistant test
package on Python 3.13.

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

The frontend test uses Node.js separately:

```bash
node --test tests/curbcharge-card.test.mjs
```

## Preparing a release

Home Assistant and HACS require a concrete version in `manifest.json`; do not
replace it with a template placeholder. To prepare a release, run the
**Release version** workflow from `main` and enter the intended GitHub tag,
such as `v0.1.2`. The workflow validates the tag, updates the manifest on a
`release/vX.Y.Z` branch, and provides a compare link for a pull request.

Review and merge that PR before creating the GitHub release from the same tag
on `main`. When the release is published, the workflow verifies that the tag
matches the manifest version and points to a commit on `main`.

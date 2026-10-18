# Development and releases

## Development

The protected `main` branch requires the `test`, `hassfest`, and `hacs` check
contexts. The Hassfest and HACS workflows use distinct job contexts so each
validator can block merges independently.

Install the test tools and run:

```bash
pip install pytest pytest-homeassistant-custom-component pytest-cov ruff mypy
pytest --cov=custom_components/curbcharge --cov-fail-under=96
ruff check .
ruff format --check .
mypy
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

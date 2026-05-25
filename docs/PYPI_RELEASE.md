# PyPI Release

Project Doctor publishes as the `project-doctor` distribution and exposes the `project-doctor` console command. The Python import package is `project_doctor`.

## Preconditions

- Confirm `https://pypi.org/project/project-doctor/` is available or controlled by this project.
- Prefer GitHub Trusted Publishing with `.github/workflows/publish.yml`. Use a PyPI API token only if Trusted Publishing is not available.
- Do not commit tokens, `.pypirc`, or environment files.
- Build from a clean checkout after tests, linting, type checks, security checks, and package audits pass.

## Trusted Publishing

Configure a PyPI trusted publisher for:

- PyPI project: `project-doctor`
- Owner: `billjamesevans`
- Repository: `project-doctor`
- Workflow: `publish.yml`
- Environment: `pypi`

Then publish by creating a GitHub release for a version tag or running the Publish to PyPI workflow manually.

## Build And Check

```bash
rm -rf build dist *.egg-info src/*.egg-info
python -m build
python -m twine check dist/*
```

## Upload

```bash
python -m twine upload dist/*
```

For token-based publishing, set credentials outside the repository:

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD="$PYPI_API_TOKEN"
python -m twine upload dist/*
```

## Post-Release Smoke Test

```bash
python -m pipx run project-doctor --version
python -m pipx run project-doctor doctor .
```

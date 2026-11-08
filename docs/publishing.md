# Building and publishing Skyrun

This repository is prepared for a `0.1.0` release but does not publish anything
when installed or tested. The distribution name is `skyrun`, the import package is
`skyrun`, and the console command is `skyrun`. The version is declared once in
`pyproject.toml`; the package reads installed metadata.

## 1. Confirm project ownership and metadata

Before the first upload, check [the PyPI project page](https://pypi.org/project/skyrun/)
and [TestPyPI](https://test.pypi.org/project/skyrun/). A missing page is not a
reservation or a guarantee that an index will accept the name. Register the
project through your own account. Names are normalized by the index, so changes
in case, hyphens, or underscores may not create a distinct name. See
[PyPI's name guidance](https://docs.pypi.org/project-management/name-retention/).

Verify the author, license, repository/documentation URLs, description, Python
range, extras, and version in `pyproject.toml`. If the chosen distribution name is
unavailable, select an owned name and update metadata, install commands, index
settings, and wheel-check expectations together. The Python import and console
command can remain `skyrun` even if the distribution name changes.

Create separate PyPI and TestPyPI accounts, verify their email addresses, and set
up the authentication methods required by those services. Their accounts and
project namespaces are independent.

## 2. Validate a clean release candidate

Work from the intended release commit in an isolated environment:

```bash
python -m venv .venv-release
source .venv-release/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[modal,dev]'
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Review the changelog and the source diff. Use an empty `dist/` directory for each
candidate so that uploads cannot accidentally include an older release. The build
command produces both a source distribution and a wheel:

```bash
python -m build
python -m twine check --strict dist/*
python tools/check_dist.py
```

`check_dist.py` inspects both distributions, installs the built wheel into a fresh
environment without the Modal extra, initializes every packaged example, and
performs offline dry runs. It also executes the standard-library hello script
locally. It does not allocate cloud compute. Its wheel installation requires
access to the package index for core dependencies. The wheel must contain the
ignore policy, `py.typed`, entry point, sample configs, ignore files, and scripts.
Neither distribution should include run artifacts, credentials, or `plan.txt`.

The source distribution includes the package, documentation, tests, release tools,
and examples so downstream users can rebuild it. `python -m build` builds the wheel
from the sdist as well as creating the sdist, exercising that path. Follow the
[Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
for the underlying distribution formats.

Before declaring a cloud-capable release, deliberately perform the live CPU smoke
and the applicable GPU/cancellation checks:

```bash
python -m modal setup
SKYRUN_TEST_MODAL=1 python -m pytest -m integration -v
SKYRUN_TEST_GPU=1 python -m pytest tests/test_integration.py::test_live_modal_gpu_sync_and_recovery -v
```

These are billed to your Modal account and are skipped by normal tests/CI. See
[validation](validation.md) for the manual GPU and recovery checklist. Record
actual results; do not describe unexecuted cloud checks as passing.

## 3. Configure Trusted Publishing

The supplied [publish workflow](../.github/workflows/publish.yml) uses GitHub OIDC
instead of storing a PyPI upload token. Configure both indexes before triggering
it. For a new project, add a **pending publisher** from each account's publishing
page. For an existing project, use its **Publishing** management section.

Use these values for this repository, adjusting them if you fork or rename it:

| Field | PyPI | TestPyPI |
| --- | --- | --- |
| Project name | `skyrun` | `skyrun` |
| Repository owner | `akaliutau` | `akaliutau` |
| Repository name | `skyrun` | `skyrun` |
| Workflow filename | `publish.yml` | `publish.yml` |
| GitHub environment | `pypi` | `testpypi` |

Create matching GitHub repository environments under **Settings → Environments**.
For production, configure required reviewers and restrict deployment to intended
release tags. The workflow gives `id-token: write` only to publish jobs; those jobs
download already-validated distributions and do not build or execute project
source. The reusable CI workflow performs the build and test work separately.

The workflow filename is the basename `publish.yml`, not a full filesystem path.
Environment names must match exactly. These details follow the official
[publisher setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/) and
[publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/) guides.

## 4. Rehearse on TestPyPI

Push your reviewed candidate branch, open **Actions → Publish → Run workflow**,
choose its ref and the `testpypi` index. The workflow runs CI, builds and checks the
distributions, then uploads them. Fix any failed checks before retrying. Each
uploaded version is immutable; bump the version for a changed candidate rather
than trying to replace uploaded files.

Test the release in a separate environment. Install runtime dependencies from
the normal PyPI index, then the exact project version from TestPyPI without
dependency resolution. This avoids resolving all dependencies from TestPyPI or
mixing project resolution across indexes:

```bash
python -m venv /tmp/skyrun-testpypi
source /tmp/skyrun-testpypi/bin/activate
python -m pip install 'pathspec>=0.12.1,<2' 'modal>=1.3.1,<1.4'
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps 'skyrun==0.1.0'
skyrun --version
skyrun init /tmp/skyrun-installed-example --example hello
skyrun --config /tmp/skyrun-installed-example/skyrun.toml --dry-run
```

Use the exact candidate version if it differs from `0.1.0`. TestPyPI may remove
old test projects; it is for rehearsal, not the permanent installation source.

## 5. Publish to PyPI

Set the final version, replace the unreleased changelog label with the release
date, and land the reviewed release commit. Create an annotated tag matching the
version exactly:

```bash
git tag -a v0.1.0 -m 'Skyrun 0.1.0'
git push origin v0.1.0
```

Create and publish a GitHub Release from that tag to trigger production
publication. Alternatively, manually run **Publish** at that tag with index
`pypi`. The workflow rejects production publication from a branch or a tag whose
version differs from `pyproject.toml`. Approve the protected GitHub environment
when requested, then verify the PyPI release files and metadata.

Install from PyPI into a fresh environment and repeat the installed-example smoke:

```bash
python -m pip install 'skyrun[modal]==0.1.0'
skyrun --version
skyrun init hello --example hello
skyrun --config hello/skyrun.toml --dry-run
```

The current workflow rebuilds at the selected ref for each invocation. Rehearsing
and releasing the same candidate therefore requires an unchanged source commit;
production artifacts are the artifacts validated in that production workflow run.
Retain the workflow's distribution artifact and release notes for review.

## Manual upload alternative

If Trusted Publishing is unavailable, use an index-scoped API token and Twine's
interactive prompt or a supported credential manager. Do not put tokens in shell
commands, repository files, or logs. At the prompt, the username is `__token__`.

```bash
python -m twine upload --repository testpypi dist/*
# After validating the exact candidate and confirming its production version:
python -m twine upload dist/*
```

Twine's default production destination is PyPI. Do not use `--skip-existing` to
hide a version collision in a release process. If an upload is partly successful,
compare every uploaded file against the validated distributions before deciding
how to complete it. Correct released mistakes with a new patch version; use the
index's yanking mechanism when a release should no longer be selected by default.
See [PyPI's publishing guide](https://docs.pypi.org/publishing/publishing-to-pypi/).

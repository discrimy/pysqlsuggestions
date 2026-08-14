# Publishing to PyPI — design

`pysqlsuggestions` is not on PyPI. `pip install pysqlsuggestions` answers 404,
and the only way to get the library is to clone it. This publishes it.

One workflow file and one metadata addition. Deliberately the smallest thing
that ships a correct artefact, because a PyPI upload cannot be replaced — only
yanked, and superseded by a version number you have already spent.

---

## 1. Context

### What exists

| | |
| --- | --- |
| `.github/workflows/ci.yml` | ruff, mypy and pytest on 3.10–3.12, plus a no-extras import check |
| `.github/workflows/pages.yml` | builds a wheel, assembles `site/`, deploys the demo to Pages on a `v*` tag |
| nine tags | `v0.1.0` … `v0.6.0` |
| two distributions | `pysqlsuggestions` (root) and `pysqlsuggestions-lsp` (`lsp/`) |

Neither distribution is published. Both names are free on PyPI — checked, both
404 — so no name has been squatted and nothing has to be reclaimed.

### What was measured, not assumed

Both distributions build cleanly with `uv build`, and the metadata is right
where it matters:

```
pysqlsuggestions-0.6.0-py3-none-any.whl
  Requires-Python: >=3.10
  License: MIT License          License-File: LICENSE
  Requires-Dist: … only under extras. No unconditional dependency.
```

The zero-dependency claim `tests/test_purity.py` enforces survives into the
published metadata, which is the form of it a user actually installs.

`pysqlsuggestions-lsp` records `Requires-Dist: pysqlsuggestions==0.6.0` — the
`[tool.uv.sources] workspace = true` override does **not** leak into the wheel.
Worth checking rather than assuming, because a leaked workspace path is
installable for the author and broken for everybody else.

### Decisions taken during brainstorming

1. **A published GitHub Release is the trigger**, not a tag push. §2.
2. **Trusted Publishing**, not an API token. §3.
3. **The library alone.** The LSP distribution is out of scope. §6.

### Rejected approaches

- **Publishing both distributions in one workflow.** The LSP package would need
  its own pending publisher, its own metadata fixes — it declares no license,
  no readme and no classifiers, so PyPI would show it unlicensed with a blank
  description — and a second job ordered after the library's. None of that is
  needed to make `pip install pysqlsuggestions` work, which is the goal.
- **A tag push as the trigger.** It is what `pages.yml` uses, and the symmetry
  is tempting. Refused because the two acts differ in cost: republishing the
  demo from a mistaken tag is free, and a mistaken PyPI upload spends a version
  number permanently. A release is a second, deliberate step, and a draft can
  sit unfinished without shipping anything.
- **TestPyPI as a dry run.** Its own pending publisher, its own release path,
  and the wheel smoke test in §2 catches what it would catch. Carried forward
  in §6 rather than refused outright.

---

## 2. The workflow

`.github/workflows/publish.yml`, triggered on `release: types: [published]`.

Two jobs, and the split is the point:

| job | permissions | does |
| --- | --- | --- |
| `build` | `contents: read` | verifies, tests, builds, uploads the artefact |
| `publish` | `id-token: write` | downloads the artefact and uploads it to PyPI |

The job holding the OIDC token runs none of this repository's code. It receives
a file and transfers it. That is what makes a short-lived upload credential safe
to mint at all, and it is why this is not one job with two steps.

### Three guards, before anything is uploaded

**The tag agrees with the version.** `pages.yml` already does exactly this, with
its reasoning recorded — a wheel takes its version from `pyproject.toml` and
never from the tag, so a tag saying `v0.7.0` on a tree reading `0.6.0` publishes
one version under another's name. The same shell, spelled for a release event:

```yaml
- name: The release and the package agree on the version
  env:
    TAG: ${{ github.event.release.tag_name }}
  run: |
    version=$(grep -m1 "^version = " pyproject.toml | cut -d"'" -f2)
    if [ "$TAG" != "v$version" ]; then
      echo "::error::release $TAG but pyproject.toml says $version"
      exit 1
    fi
```

Reusing `pages.yml`'s spelling rather than improving on it: two workflows
answering the same question two ways is how they come to disagree.

The tag reaches the shell through `env:` rather than through `${{ }}`
interpolation, which is the one place this differs from `pages.yml` — and
deliberately. `pages.yml` reads `GITHUB_REF_NAME`, an environment variable git
already constrains. A release name is a string somebody typed, and interpolating
it into a `run:` block splices it into the script before bash ever sees it.
Nobody who can publish a release here is untrusted, so this is a habit rather
than a hole; it costs two lines.

`tests/test_purity.py` already fails the build unless the version matches across
four files. The release tag is a fifth place it can disagree, and the only one
no test in the repository can see.

**The suite runs.** `uv run pytest -m 'not integration'` — a few seconds. CI
already tested the commit, so this is not about finding new bugs; it is about
the case where a release is cut from a commit whose CI never finished, or went
red and was not read.

**The built wheel installs and imports, in a clean environment.** CI's
`no-extras` job installs from source, which cannot catch a package missing from
`[tool.hatch.build.targets.wheel]` — the source tree has it either way. This
installs the actual artefact, with no extras, and imports it:

```yaml
- name: The wheel installs and imports on its own
  env:
    TAG: ${{ github.event.release.tag_name }}
  run: |
    uv venv /tmp/smoke
    VIRTUAL_ENV=/tmp/smoke uv pip install dist/*.whl
    built=$(VIRTUAL_ENV=/tmp/smoke /tmp/smoke/bin/python -c \
      "import pysqlsuggestions; print(pysqlsuggestions.__version__)")
    [ "$TAG" = "v$built" ] || { echo "::error::wheel reports $built, release is $TAG"; exit 1; }
```

The installed package is asked its version and that answer is checked against
the tag, which closes the loop: what reaches PyPI is what was verified, not
merely what was built beside it. `__version__` is a third source after
`pyproject.toml` and the tag, and `test_purity.py` already pins it to the
first — so agreeing here means all three agree.

### The upload

`pypa/gh-action-pypi-publish@release/v1`, with no `password` input — Trusted
Publishing supplies the credential. PEP 740 attestations are on by default and
stay on.

---

## 3. Trusted Publishing, and the one manual step

The workflow cannot succeed until a **pending publisher** exists, because the
project does not yet exist on PyPI and there is therefore nothing to attach a
publisher to. On pypi.org, *Your projects → Publishing → Add a pending
publisher*:

| field | value |
| --- | --- |
| PyPI project name | `pysqlsuggestions` |
| Owner | `discrimy` |
| Repository name | `pysqlsuggestions` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

And a GitHub Environment named `pypi` on the repository. The environment is not
decoration: it is half of what PyPI matches against, and adding yourself as a
required reviewer on it puts a hold-and-approve step in front of an upload that
cannot be undone.

This is the only step that cannot be committed to the repository, so it is the
one worth writing down.

---

## 4. `[project.urls]`

Added to the root `pyproject.toml`:

```toml
[project.urls]
Homepage = 'https://github.com/discrimy/pysqlsuggestions'
Demo = 'https://discrimy.github.io/pysqlsuggestions/'
```

`Homepage` rather than `GitHub` because PyPI recognises the label, renders it
with an icon and sorts it first; `Demo` is shown as written, which is what it
should say. The demo URL is the one `pages.yml` already deploys to.

Without these a PyPI page links nowhere — and this library's strongest argument
is a demo you can run in a browser without a database.

---

## 5. Testing

There is nothing here a unit test can reach: the deliverable is a workflow file,
and the thing it does happens once, off this machine, and cannot be rehearsed
against the real index without spending a version number.

What *is* verifiable locally, and will be during implementation:

- `uv build` produces both artefacts and `twine check` passes on them;
- the wheel installs into a clean venv with no extras and imports;
- the version guard's shell fragment rejects a mismatched tag and accepts a
  matching one, run directly rather than through GitHub;
- the workflow parses — `actionlint` if available, otherwise a YAML load.

The first real release is the integration test, which is why §2 spends three
guards on making it fail early rather than badly.

---

## 6. Open questions carried forward

- **`pysqlsuggestions-lsp`.** Not published. Needs a license, a readme
  (`lsp/README.md` exists and is undeclared), classifiers, and a pending
  publisher of its own. Its `==` pin on the library means it can only be
  published after a library version it names is already on the index.
- **TestPyPI.** §1.
- **The VSIX.** `editors/vscode` ships to the VS Code marketplace, a different
  registry with a different credential. Unrelated to this pipeline.
- **What the sdist ships.** Currently everything not gitignored — `CLAUDE.md`,
  `.github/`, `docker/`, `demo/`, `tests/`. Harmless and larger than it needs to
  be; a `[tool.hatch.build.targets.sdist]` include list would trim it, and
  nothing breaks if it never does.
Settled during brainstorming and no longer open: **the first upload is 0.7.0**.
See §7.

---

## 7. Cutting 0.7.0

`0.6.0` is released and tagged, and `CHANGELOG.md` holds an Unreleased section
with the `CREATE TABLE` work — a definition list that answers with types, and
the `TABLE` statement form. A minor bump is what that is: new positions answer,
nothing that answered before answers differently.

The version lives in **six** places, and every one of them is guarded by
`tests/test_purity.py`, so the gate is the checklist rather than this list
being it:

| file | what holds it |
| --- | --- |
| `pyproject.toml` | `version = '0.7.0'` |
| `src/pysqlsuggestions/__init__.py` | `__version__` |
| `lsp/pyproject.toml` | `version` |
| `lsp/pyproject.toml` | the `pysqlsuggestions==` pin |
| `lsp/pysqlsuggestions_lsp/__init__.py` | `__version__` |
| `editors/vscode/package.json` | `version` |

Two more move with them and are checked by nothing: `uv.lock`, which records
workspace member versions and is regenerated by `uv sync`, and
`editors/vscode/package-lock.json`, whose two self-referential version fields
npm rewrites.

`CHANGELOG.md`'s `## Unreleased` heading becomes `## 0.7.0`. The LSP and the
extension are bumped because `test_purity.py` requires it, **not** because
either is being published — they are versioned together with the library and
always have been.

The tag and the GitHub Release are the release act itself, and deliberately not
part of this plan: the workflow has to exist and be merged before a release can
trigger it.

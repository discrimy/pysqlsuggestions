# Publishing to PyPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pip install pysqlsuggestions` work, by publishing the library to PyPI from a GitHub Release, and cut 0.7.0 as the first upload.

**Architecture:** One new workflow file, `.github/workflows/publish.yml`, triggered on `release: published`. Two jobs split on the privilege boundary: `build` verifies and builds with no special permissions, `publish` holds the OIDC token and does nothing but transfer files. Three guards run before any upload — the release tag must match `pyproject.toml`, the suite must pass, and the built wheel must install and report the right version from a clean environment.

**Tech Stack:** GitHub Actions, `astral-sh/setup-uv@v5`, `pypa/gh-action-pypi-publish@release/v1`, PyPI Trusted Publishing (OIDC), hatchling.

## Global Constraints

- **Only `pysqlsuggestions` is published.** `pysqlsuggestions-lsp` and the VSIX are explicitly out of scope.
- **The version lives in six files, all guarded by `tests/test_purity.py`.** The gate is the checklist; do not hand-maintain a list.
- **Zero runtime dependencies.** `import pysqlsuggestions` must pull in no driver — `tests/test_purity.py` fails the build otherwise.
- Ruff with `D` enabled and mypy `strict` over `src`, `tests` and `lsp`. **Single quotes, 120 columns.**
- Comments record *why* a shape was chosen and which alternative was rejected. A change that adds behaviour without saying what it refused is out of keeping.
- Commits are `feat:`/`fix:`/`test:`/`docs:`/`refactor:`/`chore:`/`ci:` with a lowercase prose summary and a body explaining the decision.
- The gate is `./scripts/check.sh`. Integration tests need `OPENSSL_CONF` set on this machine — `C:\Program Files\Git\usr\ssl\openssl.cnf` — or ten TLS tests error on a broken conda `openssl`.

## File Structure

| File | Responsibility |
| --- | --- |
| `.github/workflows/publish.yml` | **new** — the whole pipeline: guards, build, upload |
| `pyproject.toml` | `[project.urls]`, and the version |
| `src/pysqlsuggestions/__init__.py` | `__version__` |
| `lsp/pyproject.toml` | version, and the `pysqlsuggestions==` pin |
| `lsp/pysqlsuggestions_lsp/__init__.py` | `__version__` |
| `editors/vscode/package.json` | version |
| `editors/vscode/package-lock.json` | two self-referential version fields, rewritten by npm |
| `uv.lock` | workspace member versions, rewritten by `uv sync` |
| `CHANGELOG.md` | `## Unreleased` becomes `## 0.7.0` |

---

### Task 1: `[project.urls]`

Two links on the PyPI page. Done first because it is part of what 0.7.0 ships, and it is verifiable on its own.

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `Project-URL` entries in the built wheel's METADATA. Task 3 rebuilds the same wheel with a new version.

- [ ] **Step 1: Confirm the metadata has no URLs today**

Run:

```bash
rm -rf /tmp/urlcheck && uv build --wheel -o /tmp/urlcheck -q
python -c "
import zipfile, glob
z = zipfile.ZipFile(glob.glob('/tmp/urlcheck/*.whl')[0])
name = [n for n in z.namelist() if n.endswith('METADATA')][0]
print([l for l in z.read(name).decode().splitlines() if l.startswith('Project-URL')] or 'no Project-URL lines')
"
```

Expected: `no Project-URL lines`.

- [ ] **Step 2: Add the section**

In `pyproject.toml`, directly after the closing `]` of `classifiers` and before the `# Extras are named after the driver…` comment that introduces `[project.optional-dependencies]`:

```toml
# `Homepage` rather than `GitHub`: PyPI recognises the label, renders it with an
# icon and sorts it first. `Demo` is shown as written, which is what it should
# say — and the URL is the one `.github/workflows/pages.yml` already deploys to,
# so the two cannot drift without the demo itself moving.
[project.urls]
Homepage = 'https://github.com/discrimy/pysqlsuggestions'
Demo = 'https://discrimy.github.io/pysqlsuggestions/'
```

- [ ] **Step 3: Confirm both reach the metadata**

Run:

```bash
rm -rf /tmp/urlcheck && uv build --wheel -o /tmp/urlcheck -q
python -c "
import zipfile, glob
z = zipfile.ZipFile(glob.glob('/tmp/urlcheck/*.whl')[0])
name = [n for n in z.namelist() if n.endswith('METADATA')][0]
for l in z.read(name).decode().splitlines():
    if l.startswith('Project-URL'): print(l)
"
```

Expected exactly:

```
Project-URL: Homepage, https://github.com/discrimy/pysqlsuggestions
Project-URL: Demo, https://discrimy.github.io/pysqlsuggestions/
```

- [ ] **Step 4: Run the gate**

Run: `./scripts/check.sh`

Expected: green. `pyproject.toml` is parsed by `tests/test_purity.py` and by the build; a malformed table fails here.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore: the package says where it lives"
```

Body: the PyPI page linked nowhere, and this library's strongest argument is a demo that runs in a browser with no database.

---

### Task 2: The publish workflow

**Files:**
- Create: `.github/workflows/publish.yml`

**Interfaces:**
- Consumes: `[project.urls]` from Task 1 (only in the sense that it builds the same package).
- Produces: a workflow named `publish.yml` and a GitHub Environment reference named `pypi`. Both strings are matched by the PyPI pending publisher in Task 4 and must not be changed without changing it too.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/publish.yml`:

```yaml
name: publish

# Publishes the library to PyPI when a GitHub Release is published.
#
# A release rather than a tag push, which is what `pages.yml` uses for the demo.
# The two acts differ in cost: republishing the demo from a mistaken tag is
# free, and a mistaken upload spends a version number permanently — PyPI lets a
# file be yanked, never replaced. A release is a second deliberate step, and a
# draft can sit unfinished without shipping anything.
#
# Only `pysqlsuggestions` is published here. `pysqlsuggestions-lsp` declares no
# license and no readme, which is a decision of its own; the VSIX goes to the
# VS Code marketplace, a different registry with a different credential.

on:
  release:
    types: [published]

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      # The check `pages.yml` already makes, for the reason recorded there: a
      # wheel takes its version from pyproject.toml and never from the tag, so a
      # release named v0.7.0 cut from a tree still reading 0.6.0 publishes one
      # version under another's name. `tests/test_purity.py` pins the version
      # across six files; the release tag is a seventh place it can disagree,
      # and the only one no test in the repository can see.
      #
      # The tag arrives through `env` rather than `${{ }}`, which is the one
      # place this differs from pages.yml, and deliberately. That workflow reads
      # GITHUB_REF_NAME, which git constrains. A release name is a string
      # somebody typed, and interpolating it into a `run:` block splices it into
      # the script before bash ever sees it. Nobody who can publish a release
      # here is untrusted, so this is a habit rather than a hole.
      - name: The release and the package agree on the version
        env:
          TAG: ${{ github.event.release.tag_name }}
        run: |
          version=$(grep -m1 "^version = " pyproject.toml | cut -d"'" -f2)
          if [ "$TAG" != "v$version" ]; then
            echo "::error::release $TAG but pyproject.toml says $version"
            exit 1
          fi

      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      # ci.yml already ran on this commit, so this is not about finding new
      # bugs. It is for the release cut from a commit whose CI never finished,
      # or went red and was not read. Integration tests are excluded: they need
      # three containers, and what they cover is unrelated to whether this
      # artefact is fit to publish.
      - name: The suite still passes
        run: |
          uv sync
          uv run pytest -m 'not integration' -q

      - name: Build the sdist and the wheel
        run: uv build

      # Stronger than ci.yml's `no-extras` job, which installs from source: a
      # package missing from [tool.hatch.build.targets.wheel] is present in the
      # tree either way, so that job cannot see it. This installs the artefact
      # that is about to be uploaded, with no extras, and asks it its version —
      # which closes the loop, since what reaches PyPI is then what was
      # verified rather than merely what was built beside it.
      - name: The wheel installs and imports on its own
        env:
          TAG: ${{ github.event.release.tag_name }}
        run: |
          uv venv /tmp/smoke
          VIRTUAL_ENV=/tmp/smoke uv pip install dist/*.whl
          built=$(/tmp/smoke/bin/python -c 'import pysqlsuggestions; print(pysqlsuggestions.__version__)')
          if [ "$TAG" != "v$built" ]; then
            echo "::error::the wheel reports $built, the release is $TAG"
            exit 1
          fi

      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  # A job of its own, and the split is the whole point. This one holds an OIDC
  # token PyPI will accept as proof of identity, and it runs none of this
  # repository's code: it receives files and transfers them. That is what makes
  # minting an upload credential safe at all, and it is why this is not two more
  # steps on the job above.
  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      # No `password` input: Trusted Publishing exchanges the OIDC token for a
      # short-lived credential, matched against the publisher registered on PyPI
      # for this repository, this workflow filename and this environment name.
      # Change any of those three strings and the exchange stops matching.
      #
      # PEP 740 attestations are on by default and stay on.
      - uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 2: Check it parses as YAML**

Run:

```bash
uv run --no-project --with pyyaml python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/publish.yml'))
print('jobs:', list(d['jobs']))
print('trigger:', d[True])
"
```

Expected:

```
jobs: ['build', 'publish']
trigger: {'release': {'types': ['published']}}
```

**`d[True]`, not `d['on']` — that is not a typo.** YAML 1.1 reads a bare `on` key as boolean true, so the trigger lands under `True`. GitHub Actions parses it correctly; only this check has to know.

- [ ] **Step 3: Check the guard shell actually rejects a mismatch**

The version check is the one piece of logic in the file, and it never runs locally otherwise. Run it both ways:

```bash
version=$(grep -m1 "^version = " pyproject.toml | cut -d"'" -f2)
echo "pyproject says: $version"
for TAG in "v$version" "v9.9.9" "$version"; do
  if [ "$TAG" != "v$version" ]; then echo "  $TAG -> REJECTED"; else echo "  $TAG -> accepted"; fi
done
```

Expected: `v<version> -> accepted`, `v9.9.9 -> REJECTED`, and the bare `<version>` without its `v` also `REJECTED`.

- [ ] **Step 4: Run the gate**

Run: `./scripts/check.sh`

Expected: green. Nothing in the suite reads workflow files, so this confirms the tree is unchanged rather than testing the new file.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: publish to pypi when a release is published"
```

Body should record the two decisions: a release rather than a tag push, because the acts differ in cost; and two jobs rather than one, because the job holding the OIDC token must not run repository code.

---

### Task 3: Cut 0.7.0

The version in six files, plus two lockfiles nothing guards, plus the changelog heading.

**Files:**
- Modify: `pyproject.toml`, `src/pysqlsuggestions/__init__.py`, `lsp/pyproject.toml`, `lsp/pysqlsuggestions_lsp/__init__.py`, `editors/vscode/package.json`, `editors/vscode/package-lock.json`, `uv.lock`, `CHANGELOG.md`
- Test: `tests/test_purity.py` (existing — it is the checklist)

**Interfaces:**
- Consumes: Tasks 1 and 2, both of which ship inside this version.
- Produces: `0.7.0` everywhere. Task 4 creates the release named `v0.7.0`, which the workflow's guard compares against `pyproject.toml`.

- [ ] **Step 1: Watch the purity tests fail on a partial bump**

Bump only the root, so the guards prove they are load-bearing before being trusted:

```bash
sed -i "s/^version = '0.6.0'/version = '0.7.0'/" pyproject.toml
uv run pytest tests/test_purity.py -q
```

Expected: **exactly four FAIL** —

- `test_version_is_declared_once_in_effect`
- `test_lsp_version_matches_the_library`
- `test_the_server_pins_the_library_release_it_belongs_to`
- `test_the_extension_version_matches_the_library`

`test_the_server_module_reports_the_version_it_ships_as` keeps passing, because it compares the LSP module against the LSP manifest and both still read `0.6.0`. It goes red only if Step 2 bumps one of that pair and forgets the other, which is what it is for.

- [ ] **Step 2: Bump the other five**

```bash
sed -i "s/__version__ = '0.6.0'/__version__ = '0.7.0'/" src/pysqlsuggestions/__init__.py
sed -i "s/^version = '0.6.0'/version = '0.7.0'/" lsp/pyproject.toml
sed -i "s/'pysqlsuggestions==0.6.0'/'pysqlsuggestions==0.7.0'/" lsp/pyproject.toml
sed -i "s/__version__ = '0.6.0'/__version__ = '0.7.0'/" lsp/pysqlsuggestions_lsp/__init__.py
sed -i 's/"version": "0.6.0"/"version": "0.7.0"/' editors/vscode/package.json
```

The LSP and the extension are bumped because `test_purity.py` requires it — they are versioned with the library and always have been — **not** because either is being published.

Note the `package.json` line uses double quotes and only the first match matters; verify with `grep -n '"version"' editors/vscode/package.json` that line 5 reads `0.7.0` and nothing else changed.

- [ ] **Step 3: Confirm the purity tests pass**

Run: `uv run pytest tests/test_purity.py -q`

Expected: PASS. All six places agree.

- [ ] **Step 4: Regenerate the two lockfiles**

Neither is guarded by a test, which is exactly why they are a step of their own:

```bash
uv sync
cd editors/vscode && npm install --package-lock-only && cd ../..
git diff --stat uv.lock editors/vscode/package-lock.json
```

Expected: `uv.lock` records `0.7.0` for both workspace members, and `package-lock.json` changes **only** its two self-referential `"version"` fields — lines 3 and 9. Lines 1447 and 3853 are `deep-extend` and `tunnel-agent`, third-party packages that happen to be at 0.6.0; if either moved, revert and edit lines 3 and 9 by hand.

- [ ] **Step 5: Close the changelog section**

In `CHANGELOG.md`, change the heading:

```markdown
## Unreleased
```

to:

```markdown
## 0.7.0
```

Leave every entry under it exactly as written. A minor bump is the right size: new positions answer — `CREATE TABLE t (id ⌶` offers types, `TABLE users ⌶` is a statement form — and nothing that answered before answers differently.

- [ ] **Step 6: Run the whole gate**

```bash
export OPENSSL_CONF='C:\Program Files\Git\usr\ssl\openssl.cnf'
./scripts/check.sh
```

Expected: green, with the burn-downs unchanged from before this plan:

```
corpus burn-down: 34/34 golden requests passing
report_service suite: 158/158 passing, 0 known gaps
grammar burn-down: 64/74 SELECT positions answered, 7 of the 10 gaps refused
```

- [ ] **Step 7: Confirm the built wheel reports 0.7.0**

The workflow's smoke test in miniature, run locally where it can be debugged:

```bash
rm -rf /tmp/v7 && uv build -o /tmp/v7 -q && ls /tmp/v7
uv venv /tmp/v7venv -q
VIRTUAL_ENV=/tmp/v7venv uv pip install -q /tmp/v7/pysqlsuggestions-0.7.0-py3-none-any.whl
/tmp/v7venv/bin/python -c "import pysqlsuggestions; print(pysqlsuggestions.__version__)"
```

Expected: two artefacts named `pysqlsuggestions-0.7.0.*`, and the import prints `0.7.0`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: 0.7.0"
```

Body should name what the release contains — the definition list and the `TABLE` statement form — and record that the LSP and extension versions moved because the purity tests require it rather than because either ships.

---

### Task 4: The release itself

Not code, and not automatable from here: the PyPI half cannot be committed to a repository, and the release must be cut from `main` after this branch merges.

**Files:** none.

**Interfaces:**
- Consumes: the workflow filename `publish.yml` and the environment name `pypi` from Task 2; the version `0.7.0` from Task 3.

- [ ] **Step 1: Register the pending publisher on PyPI**

The workflow cannot succeed before this exists, because the project does not yet exist on PyPI and there is nothing to attach a publisher to.

pypi.org → *Your projects* → *Publishing* → **Add a pending publisher**:

| field | value |
| --- | --- |
| PyPI project name | `pysqlsuggestions` |
| Owner | `discrimy` |
| Repository name | `pysqlsuggestions` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

- [ ] **Step 2: Create the GitHub Environment**

Repository → *Settings* → *Environments* → **New environment**, named exactly `pypi`.

Adding yourself as a required reviewer is worth it: it puts a hold-and-approve step in front of an upload that cannot be undone. The `build` job still runs and all three guards still fire before the approval is requested, so a broken release fails without ever asking.

- [ ] **Step 3: Merge, push, and tag**

```bash
git checkout main
git merge ci/publish-to-pypi
git push origin main
git tag v0.7.0
git push origin v0.7.0
```

Pushing the tag also republishes the demo — `pages.yml` triggers on `v*`. That is expected and unrelated to PyPI.

- [ ] **Step 4: Publish the release**

```bash
gh release create v0.7.0 --title 'v0.7.0' --notes-from-tag
```

or draft it in the GitHub UI from the existing `v0.7.0` tag and press **Publish release**. Publishing is what fires the workflow; a draft does nothing.

- [ ] **Step 5: Watch it, and check the result**

```bash
gh run watch
pip download pysqlsuggestions==0.7.0 --no-deps -d /tmp/frompypi && ls /tmp/frompypi
```

Expected: the run is green and the download succeeds — which is the only end-to-end proof this pipeline can have.

If the upload fails with a 403 mentioning trusted publishing, the pending publisher's five fields do not match what the workflow presented. The commonest cause is the environment name: it must be on the job **and** on the publisher.

---

## Verification

After Task 3, on the branch:

```bash
export OPENSSL_CONF='C:\Program Files\Git\usr\ssl\openssl.cnf'
./scripts/check.sh
```

Expected: green. Then `grep -rn "0\.7\.0" pyproject.toml src/pysqlsuggestions/__init__.py lsp/pyproject.toml lsp/pysqlsuggestions_lsp/__init__.py editors/vscode/package.json` should show seven matches — six version declarations plus the LSP's `pysqlsuggestions==` pin.

Task 4 is the real verification and it happens once, off this machine. That is why Task 2 spends three guards on failing early rather than badly.

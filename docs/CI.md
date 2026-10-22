# Continuous integration

Auralis has one continuous integration pipeline and one local script that
runs exactly the same checks.  A green `scripts/ci_local.sh` (or
`scripts/ci_local.ps1`) means a green pipeline.

```
.github/workflows/ci.yml        the pipeline: eleven independent jobs
.github/workflows/release.yml   the manual release build
.github/dependabot.yml          the pinned actions and the python dependencies
tools/lint_check.py             the style gate (python, trademarks, js, css)
tools/hygiene_check.py          the naming gate (no third party name)
scripts/ci_plugin_gate.py       the plug-in gate: build the modules, run the host
scripts/ci_local.sh             the same checks, on linux and macos
scripts/ci_local.ps1            the same checks, on windows
```

## The jobs

Every job is independent: it checks the tree out, installs what it needs
and asserts one thing.  No job waits for another one, so a red job never
hides the other red jobs.

| Job | Runner | Command | What it asserts |
|---|---|---|---|
| `lint` | ubuntu | `python tools/lint_check.py --require js-syntax` | the python style, the third party tokens, the javascript syntax and the stylesheets |
| `hygiene` | ubuntu | `python tools/hygiene_check.py` | no third party name, no absolute path and no stray binary in the tree |
| `unit` | ubuntu, windows | `python -m pytest tests -q -rs --ignore=tests/test_vst3_host.py` | the whole test suite passes on both platforms |
| `journey` | ubuntu | `python -m pytest tests/test_journey.py -q` | the whole path through the product, from the upload to the render |
| `schema` | ubuntu | `python tools/validate_schema.py --strict-modules`<br>`python tools/codegen_registry.py --check` | the live registry matches the canonical table, the generated artefacts match the schema and no generated file has drifted |
| `ui-css` | ubuntu | `python qa/check_css_coverage.py` | every class the interface uses is styled |
| `ui-app` | ubuntu | `python qa/verify_app.py` | the server starts and every user visible flow works |
| `ui-contracts` | ubuntu | `python -m pytest tests/test_ui_contracts.py -q` | the element ids, the css classes, the colours, the keyboard map and the localisation of the interface still line up |
| `plugin-abi` | ubuntu, windows | `python tools/build_abi.py` (the C compiler of the system)<br>`cmd /c tools\msvc.bat python plugins\abi\build_abi.py --msvc` (the Microsoft build tools)<br>`python -m pytest tests/test_plugin_abi.py -q` | the core C ABI builds, loads and behaves like a plug-in host expects |
| `plugin-build` | windows | `python scripts/ci_plugin_gate.py --strict` | the plug-ins are built, the independent host loads them, and the host gate of the test suite runs without a skip |
| `coverage` | ubuntu | `python -m pytest tests -q --ignore=tests/test_vst3_host.py --cov=aurcore --cov=aurserver` | the suite still passes under coverage; the report is attached as the artefact `coverage-report` |

The pipeline runs on every push, on every pull request and on demand
(`workflow_dispatch`).  The third party actions are pinned to a version
(`actions/checkout@v7.0.1`, `actions/setup-python@v7.0.0`,
`actions/upload-artifact@v7.0.2`); `dependabot` keeps the pins current and
opens a pull request for every update.

## The eight gates and the job of each

The repository has eight gates.  Every one of them is a job of the
pipeline, and the pipeline is red when one of them is red.

| Gate | The command | The job |
|---|---|---|
| the test suite | `python -m pytest tests -q -rs --ignore=tests/test_vst3_host.py` | `unit` |
| the schema validator | `python tools/validate_schema.py --strict-modules` | `schema` |
| the css coverage check | `python qa/check_css_coverage.py` | `ui-css` |
| the app verifier | `python qa/verify_app.py` | `ui-app` |
| the hygiene gate | `python tools/hygiene_check.py` | `hygiene` |
| the lint gate | `python tools/lint_check.py --require js-syntax` | `lint` |
| the VST3 host gate | `python scripts/ci_plugin_gate.py --strict` | `plugin-build` |
| the journey | `python -m pytest tests/test_journey.py -q` | `journey` |

The host gate is the one that is not a single command: it is the build of
the plug-ins, the run of the host against them and the host gate of the
test suite, and `scripts/ci_plugin_gate.py` is the single command that
does all three.

## The plug-in build

Before this pipeline existed, nothing in the pipeline built a plug-in, so
the forty-eight binaries and the independent host that loads them were
never checked.  The job `plugin-build` closes that hole.

It runs on a Windows runner, because the plug-in build needs the Microsoft
build tools, and it runs one command:

```
python scripts/ci_plugin_gate.py --strict
```

The command does six things, in this order:

1. it builds the core shared library (`plugins/abi/build_abi.py --msvc`),
2. it builds the independent host (`qa/vst3host`),
3. it builds the plug-in modules (`plugins/vst3native`, one module code at
   a time, `--parallel 2`),
4. it stages the shared runtime of the family
   (`scripts/vst3_runtime.py stage`), the way the build of the family does:
   the bundles hold the plug-in alone and the core and the interpreter live
   once, in the runtime directory next to them,
5. it runs the host against every module it built and reads the report the
   host writes: no fault, the classes are there, the parameters are
   published with their names and their units, audio was processed and the
   state came back,
6. it runs the host gate of the test suite
   (`python -m pytest tests/test_vst3_host.py -q -rs`) and refuses a run
   that skipped a test.

The host is told where the shared runtime is, in `AURALIS_VST3HOST_DEPS`
and in `AURALIS_RUNTIME_DIR`: the plug-in loads the core out of the
runtime itself, and in the build layout the runtime sits three levels
above the bundle.  The gate therefore proves the arrangement that ships,
not the arrangement of a bundle that carries its own copy.

The modules that are built are the modules of the registry, all forty-eight
of them, because the host gate of the test suite covers the whole family
and skips itself when the family is only partly built.  A gate that skips
is not a gate, so the gate builds the family.  The list is read out of the
registry with the same tool the build of the family uses
(`plugins/vst3/build_identity.py list --format cmake`), so the two never
drift apart.  A developer who wants one module and not forty-eight writes
`python scripts/ci_plugin_gate.py --modules AUR_DECLICK`.

The gate needs `cmake`, `ninja`, the Microsoft build tools and the VST3
SDK of the repository.  When one of them is missing the gate says so and
exits with the code 3; `--strict` turns that into a failure, because the
tool set of the runner is known and a missing tool is a broken runner.

### The home of the interpreter

The plug-in carries its own copy of the interpreter, and that copy needs the
home of the interpreter it was built against.  **The core finds that home by
itself** — it is a shared library, so it knows the directory it was loaded
from, and the home is the first of these that holds a standard library of
this interpreter:

1. the directory the core was loaded from — in the shipped arrangement this
   is the **runtime directory**, which *is* the home: it holds the core,
   `python315.dll`, `Lib/` (the standard library and
   `site-packages/numpy`) and `DLLs/` (the extension modules);
2. the directory next to it;
3. the `Python` directory next to it;
4. the `PYTHONHOME` of the environment, for a deployment that carries no
   home of its own.

A home that holds the interpreter library of another version is refused.
The home is handed to the interpreter with `PyConfig.home`, so the
environment of the host is left alone: **a user does not set a variable for
the plug-in to work**, and the pipeline must not set one either — a runner
that sets `PYTHONHOME` hides the very defect the variable was there for.

When there is no home the core does not start the interpreter at all.  It
says so, names every directory it searched, and the host reports it in the
exercise report; the interpreter's own fatal handler would take the host
down without a word.

So the pipeline does **not** set `PYTHONHOME` any more:

| where | how |
|---|---|
| `.github/workflows/ci.yml` | nothing: the `plugin-build` job runs with no `PYTHONHOME` (`ci.yml`, the "Build the plug-ins and run the host against them" step) |
| `.github/workflows/release.yml` | nothing, the same way |
| `scripts/ci_local.ps1` | still sets it to the home of the interpreter the script found — a leftover from the old arrangement, see the patch in `var/patches/` |
| `scripts/ci_local.sh` | the same |

The host can be told to let the messages of the module through instead of
catching them (`AURALIS_VST3HOST_CAPTURE=0`), which is what a developer
does when a module dies and its last word is lost with it.

### The time it adds

Measured on the machine of the author, with the build tools of the machine
and a tree that eight other agents were editing at the same time:

| Step | Time |
|---|---|
| the core shared library | 19.3 s (12.3 s warm, 25.4 s cold) |
| the host | 52.5 s (14.4 s cold) |
| one module (configure and build) | 14.0 s to 67.8 s |
| the host against one module | 2.4 s to 4.4 s |
| the gate with three modules | 288.4 s |

Measured again on the tree of the final integration wave, with the family
rebuilt against the current core (48 modules, 10.3 min of build time):

| Step | Time |
|---|---|
| the family of forty-eight modules | 10.3 min (7.1 s to 37.0 s per module) |
| the staging of the shared runtime | 8.4 s (96 files, 385 313 280 bytes) — **before** the runtime carried the standard library; see below |
| the whole build of the family, staging included | 10.5 min |

The runtime directory carries the standard library of the interpreter now,
so the staging is measured by its result rather than by a time that belongs
to the old arrangement:

| What | Measured |
|---|---|
| the home of the interpreter | 1971 files, 69 824 019 bytes (66.59 MiB) — copied into the runtime directory in **2.8 s** (`plugins/abi/interpreter_home.py`, `stage_home`) |
| the runtime directory of the build tree | 1974 files, 78 316 471 bytes (74.7 MiB) — the figure moves with every build, because the interpreter writes its byte code into the home |
| the runtime directory in the release package | 1567 files, 66 004 560 bytes (62.9 MiB) — the release builder leaves the `__pycache__` directories out |

The package's runtime is 13.2 MiB smaller than the same runtime in a build
tree whose interpreter has written its byte code into it — 11.8 MiB of that
is in numpy alone: the package ships the sources and the compiled modules,
not the cache.

On an idle machine the family of forty-eight modules is 8.1 min of build
time (9 min to 11 s per module, the measurement of the team brief), so the
job adds about ten minutes: eight minutes of building, one minute for the
core library and the host, and the rest for the runs of the host.  The
numbers are printed by the gate, so a run always says how long it took.


## The style gate

`tools/lint_check.py` uses the standard library only, so it runs everywhere
the test suite runs.  It reports every finding as
`file:line: severity [rule] message`.

| Rule | Severity | What it means |
|---|---|---|
| `py-line-length` | warning, error above the hard limit | a python line is longer than the limit |
| `py-line-length-generated` | warning | a long line in a generated file |
| `py-tab` | error | a python line carries a tab character |
| `py-trailing-whitespace` | error | a python line ends in whitespace |
| `py-missing-newline` | error | a python file does not end with a newline |
| `trademark` | error | a third party product, vendor or trademark token is used (CONTRACT §0) |
| `js-syntax` | error | a javascript source does not parse |
| `css-braces` | error | a stylesheet has unbalanced braces |
| `css-hardcoded-colour` | error | a stylesheet hard-codes a colour instead of using a variable of `aurui/css/tokens.css` |

The line length has two limits.  The soft limit is 100 characters (the line
length of `ruff` and `black` in `pyproject.toml`); a line above it is a
warning.  The hard limit is 150 characters; a line above it is an error and
fails the run.  A line that carries a single token longer than the limit
cannot be split and stays a warning.  A generated file (one that says so in
its header) is only measured against the soft limit, because the codegen
writes the parameter tables of the registry.

### The decision about the warnings

**The warnings stay warnings, and they are counted.**  A warning that is
always a warning teaches nobody to read the report, so the gate counts
them and fails when there are more of them than the budget allows.

The tree carries long lines in files that belong to other agents, so the
warnings cannot be removed by the gate alone: they have to be fixed in the
files that carry them.  Until then the count is a budget, and the budget
may only go down.  A new long line fails the gate; a fixed long line
lowers the budget.

The findings of a generated file do not count towards the budget.  They
are written by the generator, not by a hand, so no one can fix them by
re-wrapping a line.  They are still reported.

The budget and the number it was measured at are in the tool
(`DEFAULT_WARNING_BUDGET`) and can be moved with `--warning-budget`.  The
number is printed in every run:

```
  warnings: 163 counted, 57 not counted (a generated file), budget 200
```

The tree carried 163 long lines on the day this was written, in 67 files,
and 57 more in `aurcore/schema/generated.py`, which do not count.  The
budget is 200: the gate fails when the debt grows by 37 lines, and the
slack is taken away again as the lines are fixed.  The tree grows while
the work goes on, so the number has to be measured again from time to
time; the command that measures it is

```
python tools/lint_check.py --json
```

and the fields `warnings_counted` and `warnings_exempt` carry the two
numbers.  A budget that goes up is a budget that was not paid for.

The javascript check runs `node --check` on every source.  A source that
uses the module syntax is checked through a temporary `.mjs` copy, so the
check does not depend on the module detection of the node version.  Without
node the check is skipped with a message; `--require js-syntax` turns that
skip into a failure, which is what the pipeline uses, because the runner
has node.

The token list and the exemptions live at the top of the tool.  A file that
has to name a token in order to forbid it - the contract, a gate, a test of
the house rules - is listed there with its reason.

The stylesheet check counts the braces of every stylesheet and looks for
colour literals - hex colours, the colour functions and the basic named
colours - in every stylesheet but the token file.

## What is skipped, and why

A gate that skipped without saying so is the worst kind of gate, so every
skip in this pipeline is written down.

| What | When | What happens |
|---|---|---|
| the javascript syntax check | node is not on `PATH` | the check reports `SKIP` with the reason; with `--require js-syntax` it is a failure, and the pipeline uses that switch |
| the `plugin-abi` job | the build of the core library fails | the job fails.  The runner has a C compiler, so a build that fails is a failure of the tree, not a missing tool |
| the `plugin-build` job | cmake, ninja, the build tools or the SDK are missing | the gate exits with the code 3 and names the tool; `--strict` makes it a failure |
| the host gate of the test suite | a module was not built | the gate refuses a run that skipped a test, so the gate cannot pass by skipping |
| the `coverage` step of the local script | `pytest-cov` is not installed | the step is skipped with the reason; the pipeline installs `pytest-cov` itself |
| the `coverage` step of the local script | `--no-coverage` was given | the step is skipped with the reason |

A clean run of the pipeline has no skip at all: the runner has node, a C
compiler, cmake and ninja, and the plug-in build is what makes the host
gate of the test suite run instead of skip.

The release build never needs a secret.  The zips and the manifest are
built from the sources alone.  The Windows installer is opt-in
(`installer: true` on the dispatch) and is only compiled when the Inno
Setup compiler is on the machine.

## Running the checks locally

```
scripts/ci_local.sh                 # every check
scripts/ci_local.sh --no-coverage   # without the coverage run
scripts/ci_local.sh --only lint unit

scripts/ci_local.ps1                 # every check
scripts/ci_local.ps1 -NoCoverage
scripts/ci_local.ps1 -Only lint,schema
```

Both scripts run the commands of the pipeline, in the same order, and both
exit with a non-zero status as soon as one check fails.  They print the
same summary:

```
  lint               PASS  exit 0
  hygiene            PASS  exit 0
  unit               PASS  exit 0
  journey            PASS  exit 0
  schema             PASS  exit 0
  schema-codegen     PASS  exit 0
  ui-css             PASS  exit 0
  ui-app             PASS  exit 0
  ui-contracts       PASS  exit 0
  plugin-abi         PASS  exit 0
  plugin-abi-tests   PASS  exit 0
  plugin-build       PASS  exit 0
  coverage           PASS  exit 0
```

The scripts find the repository root themselves, so they can be started from
any directory.

The interpreter is the one of the project, not the one that happens to be
first on the `PATH`: the scripts look for `AURALIS_PYTHON` first, then for
the `pythoncore` build under `%LOCALAPPDATA%\Python`, and only then for
the `PATH`.  The run says which interpreter it uses and stops with a
message when that interpreter cannot import `numpy` and `pytest`, because
six steps would otherwise fail with the same message.

The core library is built with the compiler of the machine: the Microsoft
build tools on Windows, the C compiler of the system everywhere else.  The
pipeline builds it the same way on the runner of the same platform.

## How to add a check

1. **Write the check as a tool.**  A tool lives in `tools/`, uses the
   standard library only, reports `file:line` and exits with a non-zero
   status when it finds something.  `tools/lint_check.py` is the model: it
   has a `--json` mode, a `--only` switch and a documented exit code.
2. **Give it a job.**  Add a job to `.github/workflows/ci.yml` that runs the
   tool.  A job needs a checkout, a python interpreter and the dependencies
   it needs; it must not need a secret.  A tool that the runner does not
   have is a broken runner, so the job fails and says which tool it wanted.
3. **Run it locally too.**  Add the same command to `scripts/ci_local.sh`
   and `scripts/ci_local.ps1`, so that the local script keeps matching the
   pipeline.  Both scripts have to run the same command with the same
   arguments.
4. **Add it to the release build** when it gates a release: the release
   workflow runs the checks of the pipeline before it builds the packages.
5. **Write it down.**  Add a row to the table above, and say what the check
   asserts and when it is skipped.

The drift gate is the one check that must never be weakened: the generated
artefacts are written by `tools/codegen_registry.py`, and
`python tools/codegen_registry.py --check` fails when a committed file no
longer matches a fresh run.  Fix a drift with
`python tools/codegen_registry.py`, never by hand.

## The release build

`scripts/build_release.py` assembles the release tree, writes the manifest
with the SHA-256 of every file, writes the zip and compiles the installer
when the Inno Setup compiler is available.  The release workflow runs the
gates of the pipeline first, then the builder, and attaches the zips and
the manifest as the artefacts `release-packages` and `release-manifest`.

#!/usr/bin/env bash
#
# scripts/ci_local.sh - run the continuous integration of Auralis locally.
#
# The script runs exactly the checks of .github/workflows/ci.yml, in the same
# order and with the same commands, so a green run here means a green
# pipeline.  It exits with a non-zero status as soon as one check fails.
#
# The steps and the jobs of the pipeline they belong to:
#
#     lint            lint          python tools/lint_check.py --require js-syntax
#     hygiene         hygiene       python tools/hygiene_check.py
#     unit            unit          python -m pytest tests -q -rs --ignore=tests/test_vst3_host.py
#     journey         journey       python -m pytest tests/test_journey.py -q
#     schema          schema        python tools/validate_schema.py --strict-modules
#     schema-codegen  schema        python tools/codegen_registry.py --check
#     ui-css          ui-css        python qa/check_css_coverage.py
#     ui-app          ui-app        python qa/verify_app.py
#     ui-contracts    ui-contracts  python -m pytest tests/test_ui_contracts.py -q
#     plugin-abi      plugin-abi    the core library, then the conformance tests
#     plugin-build    plugin-build  python scripts/ci_plugin_gate.py --strict
#     coverage        coverage      the suite with the coverage report
#
# Usage:
#     scripts/ci_local.sh                 # every check
#     scripts/ci_local.sh --no-coverage   # without the coverage run
#     scripts/ci_local.sh --only lint unit
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

# ------------------------------------------------------------------ options --
WITH_COVERAGE=1
ONLY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --no-coverage)
            WITH_COVERAGE=0
            ;;
        --only)
            shift
            added=""
            while [ $# -gt 0 ]; do
                case "$1" in
                    -*) break ;;
                esac
                added="$added $1"
                shift
            done
            if [ -z "$added" ]; then
                echo "--only needs at least one step" >&2
                exit 2
            fi
            ONLY="$ONLY$added"
            continue
            ;;
        -h | --help)
            sed -n '2,25p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done
if [ -n "$ONLY" ]; then
    ONLY=" ${ONLY//,/ } "
fi

# ------------------------------------------------------------------ python ---
# The interpreter of the project is the one that has the dependencies of
# requirements.txt, not the one that happens to be first on the PATH.
# AURALIS_PYTHON wins over everything.
PYTHON="${AURALIS_PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "no python interpreter was found on PATH" >&2
    exit 2
fi
echo "python : $PYTHON ($("$PYTHON" --version 2>&1))"
echo "root   : $ROOT"

# No PYTHONHOME: the plug-in carries the standard library of its own
# interpreter and the core finds it by itself - the runtime directory of
# the family IS the home.  A run that sets the variable hides the defect
# the variable was there for.

# A missing module makes six steps fail with the same message.  The run
# says it once, at the top, and stops.
MISSING="$("$PYTHON" -c "
import importlib.util
print('|'.join(n for n in ('numpy', 'pytest') if importlib.util.find_spec(n) is None))
" 2>/dev/null)"
if [ -n "$MISSING" ]; then
    echo "the interpreter $PYTHON cannot import: $MISSING" >&2
    echo "the checks need the dependencies of requirements.txt:" >&2
    echo "    $PYTHON -m pip install -r requirements.txt" >&2
    exit 2
fi

# ------------------------------------------------------------------ runner ---
SUMMARY=""
FAILED=0

record() {
    local line
    line="$(printf '  %-18s %-5s %s' "$1" "$2" "$3")"
    SUMMARY="${SUMMARY}${line}
"
}

wants() {
    # Is the step wanted?  With no --only every step is wanted.
    if [ -z "$ONLY" ]; then
        return 0
    fi
    case "$ONLY" in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

run_step() {
    local name="$1"
    shift
    echo ""
    echo "==> $name"
    local code=0
    "$@" || code=$?
    if [ "$code" -eq 0 ]; then
        record "$name" "PASS" "exit 0"
        return 0
    fi
    record "$name" "FAIL" "exit $code"
    FAILED=1
    return "$code"
}

skip_step() {
    local name="$1"
    local reason="$2"
    echo ""
    echo "==> $name"
    echo "    SKIPPED: $reason"
    record "$name" "SKIP" "$reason"
}

# ------------------------------------------------------------------- checks ---
if wants lint; then
    run_step "lint" "$PYTHON" tools/lint_check.py --require js-syntax
fi

if wants hygiene; then
    run_step "hygiene" "$PYTHON" tools/hygiene_check.py
fi

if wants unit; then
    run_step "unit" "$PYTHON" -m pytest tests -q -rs --ignore=tests/test_vst3_host.py
fi

if wants journey; then
    run_step "journey" "$PYTHON" -m pytest tests/test_journey.py -q
fi

if wants schema; then
    run_step "schema" "$PYTHON" tools/validate_schema.py --strict-modules
    run_step "schema-codegen" "$PYTHON" tools/codegen_registry.py --check
fi

if wants ui-css; then
    run_step "ui-css" "$PYTHON" qa/check_css_coverage.py
fi

if wants ui-app; then
    run_step "ui-app" "$PYTHON" qa/verify_app.py
fi

if wants ui-contracts; then
    run_step "ui-contracts" "$PYTHON" -m pytest tests/test_ui_contracts.py -q
fi

if wants plugin-abi; then
    # The core library needs a C compiler; the build fails when there is
    # none, and a failure is a failure.
    echo ""
    echo "==> plugin-abi"
    if "$PYTHON" tools/build_abi.py; then
        run_step "plugin-abi-tests" "$PYTHON" -m pytest tests/test_plugin_abi.py -q
    else
        record "plugin-abi" "FAIL" "the core library did not build"
        FAILED=1
    fi
fi

if wants plugin-build; then
    # The gate builds the core library, the host and the plug-in modules,
    # runs the host against every module and then runs the host gate of the
    # test suite.  --strict: a missing tool is a failure, not a skip.
    run_step "plugin-build" "$PYTHON" scripts/ci_plugin_gate.py --strict
fi

if wants coverage; then
    if [ "$WITH_COVERAGE" -eq 0 ]; then
        skip_step "coverage" "disabled with --no-coverage"
    elif "$PYTHON" -c "import pytest_cov" >/dev/null 2>&1; then
        mkdir -p var/ci
        run_step "coverage" "$PYTHON" -m pytest tests -q \
            --ignore=tests/test_vst3_host.py \
            --cov=aurcore \
            --cov=aurserver \
            --cov-report=term \
            --cov-report=xml:var/ci/coverage.xml \
            --cov-report=html:var/ci/htmlcov
    else
        skip_step "coverage" "pytest-cov is not installed (pip install pytest-cov)"
    fi
fi

# ------------------------------------------------------------------ summary ---
echo ""
echo "=============================================================================="
echo " AURALIS LOCAL CI"
echo "=============================================================================="
if [ -z "$SUMMARY" ]; then
    echo "no step was selected"
    echo "------------------------------------------------------------------------------"
    echo "RESULT: FAIL"
    exit 1
fi
printf '%s' "$SUMMARY"
echo "------------------------------------------------------------------------------"
if [ "$FAILED" -ne 0 ]; then
    echo "RESULT: FAIL"
    exit 1
fi
echo "RESULT: PASS"
exit 0

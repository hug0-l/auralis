"""
aurmcp.resources — the resources the Auralis model context protocol server
offers.

A resource is a document the agent can **read**.  The resources of this
server are the documents of the product itself: the catalogue of the
modules, the canonical parameter table, the corpus with its ground truth,
the benchmark report, the model library and the documents of the tree.

Three rules are kept here:

* a resource says what it holds **and what it does not hold**, because the
  agent reads the description to decide whether to read the resource;
* a document that is not in the tree is said so in the description, and the
  read answers with the same sentence instead of failing;
* nothing is written into the tree by a read.

The readers are the readers of the tools: the catalogue, the corpus and the
model library are read through the same code, so the resource and the tool
can never disagree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

try:  # pragma: no cover - the protocol core is built in parallel
    from .protocol import Resource, ToolError
except ImportError:  # pragma: no cover - the fallback keeps the resources importable
    from .tools import Resource, ToolError

from .tools import (
    _module_infos,
    _repo_root,
    _tool_benchmark,
    _tool_list_corpus_cases,
    _tool_list_models,
)

__all__ = ["RESOURCES", "RESOURCE_BY_URI"]


#: The documents of the tree the resources point at, with the file that holds
#: each of them.  A document that is not in the tree is not an error: the
#: description of the resource says so.
_DOCUMENTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "auralis://docs/architecture",
        "Architecture of the tree",
        "docs/ARCHITECTURE.md",
        "how the core, the server, the interface and the plug-ins fit together",
    ),
    (
        "auralis://docs/status",
        "Status of the product",
        "docs/ALPHA_STATUS.md",
        "what the product does today and what it does not do yet",
    ),
    (
        "auralis://docs/benchmark-notes",
        "Notes of the benchmark",
        "docs/BENCHMARK_NOTES.md",
        "how the benchmark was built and what its numbers mean",
    ),
    (
        "auralis://docs/restoration-reference",
        "Restoration reference",
        "docs/RESTORATION_REFERENCE.md",
        "the reference of the restoration modules and of the rules they are held to",
    ),
)


def _doc_path(relative: str) -> Path:
    """The path of a document of the tree."""
    return _repo_root() / relative


def _document_reader(relative: str):
    """A reader that answers with the text of a document of the tree."""

    def read() -> str:
        path = _doc_path(relative)
        if not path.is_file():
            return (
                f"The document {relative} is not in the tree of this checkout. "
                "There is nothing to read."
            )
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            return f"The document {relative} cannot be read: {error}"

    return read


def _document_description(relative: str, what: str) -> str:
    """The description of a document: it says whether the document is there."""
    path = _doc_path(relative)
    if path.is_file():
        size = path.stat().st_size
        return (
            f"The document of the tree that says {what} ({relative}, "
            f"{size} bytes). It is the document itself, verbatim; it does not "
            "hold the numbers of a measurement, and it does not hold the "
            "catalogue of the modules (auralis://modules does)."
        )
    return (
        f"The document that would say {what} ({relative}) is NOT in the tree of "
        "this checkout. Reading this resource answers with that sentence "
        "instead of the document."
    )


# ---------------------------------------------------------------------------
# the readers of the generated resources
# ---------------------------------------------------------------------------
def _modules_markdown() -> str:
    """The catalogue of the modules, as markdown."""
    infos = _module_infos()
    lines = [
        "# The modules of Auralis",
        "",
        f"{len(infos)} modules. The code of a module is its name everywhere: "
        "it is the key of a chain, of a preset and of every tool of the "
        "server.",
        "",
        "| code | name | category | edition | parameters | learn | analysis | model |",
        "| --- | --- | --- | --- | ---: | :---: | :---: | :---: |",
    ]
    for info in infos:
        lines.append(
            f"| `{info.code}` | {info.display_name} | {info.category} | "
            f"{info.edition} | {len(info.params)} | "
            f"{'yes' if info.learn else 'no'} | "
            f"{'yes' if info.analysis else 'no'} | "
            f"{'yes' if info.ml else 'no'} |"
        )
    lines.append("")
    lines.append(
        "The parameters of a module are in the parameter table "
        "(auralis://parameters); the modules are described one by one by the "
        "tool describe_module."
    )
    return "\n".join(lines)


def _parameters_markdown() -> str:
    """The canonical parameter table, as markdown."""
    path = _repo_root() / "aurcore" / "schema" / "params.tsv"
    if not path.is_file():
        return (
            "The canonical parameter table (aurcore/schema/params.tsv) is not "
            "in the tree of this checkout. There is nothing to read."
        )
    rows: list[list[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        rows.append(raw.split("\t"))
    lines = [
        "# The parameter table of Auralis",
        "",
        "The canonical table of the tree (aurcore/schema/params.tsv). The "
        "values are in natural units (dB, Hz, ms, %); the interface maps them "
        "to a knob position and the core never sees a normalised value. The "
        "name of a parameter is its identity: it is the key of a preset and of "
        "a chain.",
        "",
    ]
    module = ""
    for row in rows:
        if len(row) < 17:
            continue
        code = row[0]
        if code != module:
            module = code
            lines.append("")
            lines.append(f"## {code} — {row[1]}")
            lines.append("")
            lines.append(
                "| parameter | type | default | range | unit | curve | choices | "
                "group | aliases | help |"
            )
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        name = row[5]
        kind = row[6]
        default = row[7]
        low = row[8]
        high = row[9]
        curve = row[10]
        unit = row[12]
        choices = row[13].replace("|", ", ")
        group = row[14]
        help_text = row[15]
        aliases = row[16].replace("|", ", ")
        span = f"{low}..{high}"
        lines.append(
            f"| {name} | {kind} | {default} | {span} | {unit} | {curve} | "
            f"{choices} | {group} | {aliases} | {help_text} |"
        )
    lines.append("")
    lines.append(
        "The aliases are the names the parameter had before it was renamed: a "
        "document that carries an old name still loads, and the old name is "
        "still accepted."
    )
    return "\n".join(lines)


def _corpus_markdown() -> str:
    """The corpus and the ground truth of every case, as markdown."""
    answer = _tool_list_corpus_cases({})
    lines = [
        "# The corpus of Auralis",
        "",
        f"{answer['count']} cases. A case is a clip whose truth is known: the "
        "bands that carry nothing but the artefact, the partials that carry "
        "wanted content, the events, the regions and the levels. The "
        "measurements of a case are taken with the bands and the partials the "
        "manifest names, in the mix the manifest names for them.",
        "",
    ]
    for case in answer["cases"]:
        truth = case["ground_truth"]
        lines.append(f"## {case['id']}")
        lines.append("")
        lines.append(
            f"- file: `{case['file']}` — {case['duration_s']} s, "
            f"{case['sample_rate']} Hz, {case['channels']} channels"
        )
        lines.append(f"- tags: {', '.join(case['tags'])}")
        if case["benchmark_case"]:
            lines.append(
                f"- the benchmark measures this case; the bands that carry the "
                f"artefact are {', '.join(case['artefact_bands'])} and the "
                f"partials that carry wanted content are "
                f"{', '.join(case['wanted_partials'])}"
            )
        if truth["bands"]:
            lines.append("")
            lines.append("| band | hertz | region (s) | mix | level (dBFS) |")
            lines.append("| --- | --- | --- | --- | ---: |")
            for band in truth["bands"]:
                lines.append(
                    f"| {band['name']} | {band['band_hz']} | {band['region_s']} | "
                    f"{band['mix']} | {band['level_dbfs']} |"
                )
        if truth["frequencies"]:
            lines.append("")
            lines.append("| partial | hertz | region (s) | mix | tolerance (hertz) |")
            lines.append("| --- | --- | --- | --- | ---: |")
            for item in truth["frequencies"]:
                lines.append(
                    f"| {item['name']} | {item['hz']} | {item['region_s']} | "
                    f"{item['mix']} | {item['tolerance_hz']} |"
                )
        if truth["events"]:
            lines.append("")
            lines.append("| event | from (s) | to (s) | what |")
            lines.append("| --- | ---: | ---: | --- |")
            for event in truth["events"]:
                lines.append(
                    f"| {event['type']} | {event['start_s']} | {event['end_s']} | "
                    f"{event['label']} |"
                )
        if truth["regions"]:
            lines.append("")
            lines.append(
                "regions: "
                + ", ".join(f"{name} {value}" for name, value in truth["regions"].items())
            )
        if truth["levels"]:
            lines.append("")
            lines.append(
                "levels: "
                + ", ".join(f"{name} {value}" for name, value in truth["levels"].items())
            )
        lines.append("")
    return "\n".join(lines)


def _models_markdown() -> str:
    """The model library, as markdown."""
    answer = _tool_list_models({})
    lines = [
        "# The models of Auralis",
        "",
        answer["rule"] + ".",
        "",
        "| model | version | trained on | problem | cost of a 64 sample block |",
        "| --- | --- | --- | --- | --- |",
    ]
    for model in answer["models"]:
        cost = model["cost_per_64_sample_block"]
        if cost is None:
            figure = "no budget recorded"
        else:
            figure = (
                f"{cost['transform_equivalents']} transforms "
                f"({cost['reference_microseconds']} us, "
                f"{cost['share_of_the_realtime_budget_percent']} % of the budget)"
            )
        lines.append(
            f"| `{model['name']}` | {model['version']} | {model['trained_on']} | "
            f"{model['problem']} | {figure} |"
        )
    lines.append("")
    for model in answer["models"]:
        lines.append(f"## {model['name']}")
        lines.append("")
        lines.append(f"- file: `{model['path']}`")
        lines.append(f"- task: {model['task']}")
        lines.append(f"- variant: {model['variant']}")
        lines.append(f"- path of the product: {model['kind']}")
        lines.append(f"- cost: {model['cost_note']}")
        lines.append("")
    return "\n".join(lines)


def _benchmark_text() -> str:
    """The benchmark report, as the runner prints it."""
    try:
        answer = _tool_benchmark({})
    except ToolError as error:
        return f"The benchmark could not be run: {error}"
    except Exception as error:  # noqa: BLE001 - a read must not fail
        return f"The benchmark could not be run: {type(error).__name__}: {error}"
    return str(answer.get("table") or "")


def _safe(reader: Callable[[], str]) -> Callable[[], str]:
    """A reader that answers with the failure instead of raising."""

    def read() -> str:
        try:
            return reader()
        except ToolError as error:
            return str(error)
        except Exception as error:  # noqa: BLE001 - a read must not fail
            return f"The resource could not be read: {type(error).__name__}: {error}"

    return read


# ---------------------------------------------------------------------------
# the resources
# ---------------------------------------------------------------------------
RESOURCES: list[Resource] = [
    Resource(
        uri="auralis://modules",
        name="Module catalogue",
        description=(
            "The catalogue of the modules of the product as markdown: for "
            "every module its code, its display name, its category, its "
            "edition and how many parameters it has. It is the index of the "
            "codes that set_chain and describe_module take. It does not hold "
            "the parameters themselves (auralis://parameters does), and it "
            "does not hold what a module does to the audio."
        ),
        mime_type="text/markdown",
        reader=_safe(_modules_markdown),
    ),
    Resource(
        uri="auralis://parameters",
        name="Parameter table",
        description=(
            "The canonical parameter table of the tree as markdown: for every "
            "module and every parameter its name, its type, its default, its "
            "range, its unit, its choices, its group and its help sentence. "
            "The values are in natural units (dB, Hz, ms, %). It does not "
            "hold the code of a module that has no parameter, and it does "
            "not hold the state of a chain."
        ),
        mime_type="text/markdown",
        reader=_safe(_parameters_markdown),
    ),
    Resource(
        uri="auralis://corpus",
        name="Corpus and ground truth",
        description=(
            "The corpus of the product as markdown: every case with its "
            "length, its tags and its ground truth — the bands that carry "
            "nothing but the artefact, the partials that carry wanted "
            "content, the events, the regions and the levels. It is the "
            "ground truth the measurement is taken against. It does not hold "
            "the audio of the cases, and it does not hold the result of a "
            "measurement."
        ),
        mime_type="text/markdown",
        reader=_safe(_corpus_markdown),
    ),
    Resource(
        uri="auralis://benchmark",
        name="Benchmark report",
        description=(
            "The report of the benchmark of the project as the runner prints "
            "it: our numbers against the recorded reference numbers, the "
            "verdict of every row and the gap. Reading this resource runs "
            "the benchmark, which takes about half a minute, and the run is "
            "deterministic, so two runs give the same numbers. It does not "
            "hold the measurements of a chain of the agent's own, and it does "
            "not fail when a module is behind a reference number."
        ),
        mime_type="text/plain",
        reader=_safe(_benchmark_text),
    ),
    Resource(
        uri="auralis://models",
        name="Model library",
        description=(
            "The library of the shipped models as markdown: the name, the "
            "version, the date each model was trained, the problem it "
            "solves, its task and the cost of one 64 sample block of it. A "
            "model belongs to the offline path: the real time path never "
            "loads one. It does not load a model and it does not run one."
        ),
        mime_type="text/markdown",
        reader=_safe(_models_markdown),
    ),
]

for _uri, _name, _relative, _what in _DOCUMENTS:
    RESOURCES.append(
        Resource(
            uri=_uri,
            name=_name,
            description=_document_description(_relative, _what),
            mime_type="text/markdown",
            reader=_safe(_document_reader(_relative)),
        )
    )

del _uri, _name, _relative, _what


#: The resources, keyed by their uri.
RESOURCE_BY_URI: dict[str, Resource] = {resource.uri: resource for resource in RESOURCES}

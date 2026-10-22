"""
aurcore.serial — the persistence layer of the Auralis core.

Three artefact kinds, one shared value format:

``auralis.preset``
    a single module with its parameters and its state
    (:mod:`aurcore.serial.preset_json`).

``auralis.chain``
    the module chain of a session, in signal order
    (:mod:`aurcore.serial.chain_json`).

``auralis.document``
    a session: audio reference, chain and view state
    (:mod:`aurcore.serial.document`).

Every scalar in every artefact is wrapped in the typed-value envelope of
:mod:`aurcore.serial.typed_value`, and every artefact carries a ``version``
that :mod:`aurcore.serial.migrate` knows how to upgrade.
"""
from __future__ import annotations

from . import typed_value
from .typed_value import unwrap, unwrap_all, wrap, wrap_all
from .migrate import (
    CURRENT_VERSION,
    MigrationRegistry,
    chain_v0_to_v1,
    document_v0_to_v1,
    migrate,
    migrate_chain,
    migrate_document,
    migrate_preset,
    migrations_applied,
    preset_v0_to_v1,
    register,
)
from .preset_json import (
    PARAM_ID_KEY,
    PARAM_SIG_KEY,
    PRESET_FORMAT,
    PRESET_VERSION,
    apply_parameters,
    apply_preset,
    list_presets,
    load_preset,
    module_from_preset,
    param_entry,
    param_entry_id,
    param_entry_signature,
    parameter_identity,
    parameter_signature,
    preset_from_module,
    save_preset,
    with_identities,
)
from .chain_json import (
    CHAIN_FORMAT,
    CHAIN_VERSION,
    chain_from_dict,
    chain_to_dict,
    describe_chain,
    load_chain,
    reorder_chain,
    save_chain,
)
from .document import (
    DOCUMENT_FORMAT,
    DOCUMENT_VERSION,
    document_audio,
    document_from_dict,
    document_view,
    load_document,
    save_document,
)

__all__ = [
    # typed values
    "typed_value",
    "wrap",
    "unwrap",
    "wrap_all",
    "unwrap_all",
    # presets
    "PRESET_FORMAT",
    "PRESET_VERSION",
    "save_preset",
    "load_preset",
    "apply_preset",
    "module_from_preset",
    "preset_from_module",
    "list_presets",
    # the identity of a parameter
    "PARAM_ID_KEY",
    "PARAM_SIG_KEY",
    "parameter_signature",
    "param_entry",
    "param_entry_id",
    "param_entry_signature",
    "parameter_identity",
    "with_identities",
    "apply_parameters",
    # chains
    "CHAIN_FORMAT",
    "CHAIN_VERSION",
    "save_chain",
    "load_chain",
    "chain_to_dict",
    "chain_from_dict",
    "reorder_chain",
    "describe_chain",
    # documents
    "DOCUMENT_FORMAT",
    "DOCUMENT_VERSION",
    "save_document",
    "load_document",
    "document_from_dict",
    "document_audio",
    "document_view",
    # migrations
    "CURRENT_VERSION",
    "MigrationRegistry",
    "register",
    "migrate",
    "migrate_preset",
    "migrate_chain",
    "migrate_document",
    "migrations_applied",
    "preset_v0_to_v1",
    "chain_v0_to_v1",
    "document_v0_to_v1",
]

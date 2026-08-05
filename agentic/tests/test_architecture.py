# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Architectural import contracts for the agentic package.

These tests inspect import graphs and forbidden symbols. They must run first
in CI and fail fast — a contract break is a build failure, not a review note.

Contracts enforced here (also summarised in ``agentic/README.md``):

* AC1 — ``trust/``, ``scenario/``, ``benchmark/`` import nothing from ``PiCN.*``
* AC2 — only ``agentic.adapters.picn`` may import ``PiCN.*`` broadly;
  ``agentic.agentic_layer`` may import ``PiCN.Processes`` / ``PiCN.Packets``
  only (async layer wiring)
* AC3 — no LLM client on the forwarding path
* AC4 — ``port/`` defines no substrate-specific types
* AC5 — agentic modules import the port, never adapter internals
* AC6 — ``binding/`` may verify/reference attestation, never mint it
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

AGENTIC_ROOT = Path(__file__).resolve().parents[1]

# Modules that may import PiCN.* (AC2). Everything else must not.
# ``agentic.agentic_layer`` is a narrow exception: it subclasses
# AsyncLayerProcess and may touch packet types, but must not import
# ``PiCN.Layers.*`` or other stack internals (those stay in adapters.picn).
PICN_IMPORT_ALLOWLIST = frozenset(
    {
        "agentic.adapters.picn",
    }
)

AGENTIC_LAYER_PICN_ALLOWLIST = frozenset(
    {
        "PiCN.Processes",
        "PiCN.Packets",
    }
)

# Packages that must never import PiCN.* (AC1).
PICN_FREE_PACKAGES = frozenset(
    {
        "agentic.trust",
        "agentic.scenario",
        "agentic.benchmark",
    }
)

# Forwarding-path packages: LLM clients are forbidden here (AC3).
# Binding is the only place pydantic_ai / openai / anthropic / ollama may appear.
FORWARDING_PATH_PACKAGES = frozenset(
    {
        "agentic.port",
        "agentic.adapters",
        "agentic.agentic_layer",
        "agentic.trust",
    }
)

LLM_CLIENT_MODULES = frozenset(
    {
        "pydantic_ai",
        "openai",
        "anthropic",
        "ollama",
    }
)

# Substrate-specific type / module names that must not appear in agentic.port (AC4).
PORT_FORBIDDEN_NAMES = frozenset(
    {
        "Interest",
        "Content",
        "Nack",
        "FaceID",
        "FaceIDTable",
        "BasicICNLayer",
        "AsyncBasicICNLayer",
        "NFNLayer",
        "BasicNFNLayer",
        "AsyncBasicNFNLayer",
        "LayerStack",
        "AsyncLayerStack",
    }
)

# Agentic packages that must import the port interface, never adapter internals (AC5).
PORT_CONSUMERS = frozenset(
    {
        "agentic.agentic_layer",
        "agentic.trust",
        "agentic.binding",
        "agentic.scenario",
        "agentic.benchmark",
    }
)

ADAPTER_INTERNAL_PREFIXES = (
    "agentic.adapters.picn",
    "agentic.adapters.mock",
)

# Binding may reference verification / lookup APIs, never minting (AC6).
ATTESTATION_MINTING_NAMES = frozenset(
    {
        "issue_quote",
        "mint_quote",
        "sign_quote",
        "create_quote",
        "IssueQuote",
        "MintQuote",
        "SignQuote",
    }
)


def _iter_python_files(package_dir: Path) -> Iterable[Path]:
    """Yield ``.py`` files under ``package_dir``, skipping caches."""
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _module_name(path: Path) -> str:
    """Map a filesystem path under ``agentic/`` to a dotted module name."""
    relative = path.relative_to(AGENTIC_ROOT.parent)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(module: str, path: Path, level: int, submodule: str | None) -> str:
    """Resolve a relative import to a dotted absolute module name (PEP 328)."""
    parts = module.split(".")
    if path.name != "__init__.py":
        parts = parts[:-1]
    if level > len(parts):
        return submodule or ""
    base = parts[: len(parts) - (level - 1)]
    if submodule:
        return ".".join(base + submodule.split("."))
    return ".".join(base)


def _collect_imports(path: Path) -> set[str]:
    """Return dotted import names referenced by ``path``.

    Handles ``import x.y`` and ``from x.y import z``. Relative imports are
    resolved against the module's package.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module = _module_name(path)
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                resolved = _resolve_relative(module, path, node.level, node.module)
                if resolved:
                    found.add(resolved)
            elif node.module:
                found.add(node.module)
    return found


def _imports_match(imported: str, prefix: str) -> bool:
    """True if ``imported`` is ``prefix`` or a sub-module of it."""
    return imported == prefix or imported.startswith(prefix + ".")


def _module_under(module: str, package: str) -> bool:
    """True if ``module`` lives in ``package`` (inclusive)."""
    return module == package or module.startswith(package + ".")


def _defined_names(path: Path) -> set[str]:
    """Top-level and class/function names defined in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _all_agentic_modules() -> list[tuple[str, Path]]:
    return [(_module_name(p), p) for p in _iter_python_files(AGENTIC_ROOT)]


# ---------------------------------------------------------------------------
# AC1 — trust / scenario / benchmark never import PiCN.*
# ---------------------------------------------------------------------------


def test_ac1_picn_free_packages_import_nothing_from_picn() -> None:
    violations: list[str] = []
    for module, path in _all_agentic_modules():
        if not any(_module_under(module, pkg) for pkg in PICN_FREE_PACKAGES):
            continue
        for imported in _collect_imports(path):
            if _imports_match(imported, "PiCN"):
                violations.append(f"{module} imports {imported}")
    assert violations == [], "AC1 violated:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# AC2 — only the PiCN adapter may import PiCN.*
# ---------------------------------------------------------------------------


def test_ac2_only_picn_adapter_imports_picn() -> None:
    violations: list[str] = []
    for module, path in _all_agentic_modules():
        if any(_module_under(module, allowed) for allowed in PICN_IMPORT_ALLOWLIST):
            continue
        # Workflow / integration tests may drive a real stack; production
        # modules remain constrained.
        if _module_under(module, "agentic.tests"):
            continue
        for imported in _collect_imports(path):
            if not _imports_match(imported, "PiCN"):
                continue
            if _module_under(module, "agentic.agentic_layer"):
                if any(
                    _imports_match(imported, allowed)
                    for allowed in AGENTIC_LAYER_PICN_ALLOWLIST
                ):
                    continue
                violations.append(
                    f"{module} imports {imported} "
                    f"(agentic_layer may only import {sorted(AGENTIC_LAYER_PICN_ALLOWLIST)})"
                )
            else:
                violations.append(f"{module} imports {imported}")
    assert violations == [], "AC2 violated:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# AC3 — no LLM client on the forwarding path
# ---------------------------------------------------------------------------


def test_ac3_no_llm_client_on_forwarding_path() -> None:
    violations: list[str] = []
    for module, path in _all_agentic_modules():
        if not any(_module_under(module, pkg) for pkg in FORWARDING_PATH_PACKAGES):
            continue
        for imported in _collect_imports(path):
            root = imported.split(".", 1)[0]
            if root in LLM_CLIENT_MODULES:
                violations.append(f"{module} imports {imported}")
    assert violations == [], "AC3 violated:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# AC4 — port defines no substrate-specific types / imports
# ---------------------------------------------------------------------------


def test_ac4_port_has_no_substrate_types_or_picn_imports() -> None:
    violations: list[str] = []
    for module, path in _all_agentic_modules():
        if not _module_under(module, "agentic.port"):
            continue
        for imported in _collect_imports(path):
            if _imports_match(imported, "PiCN"):
                violations.append(f"{module} imports {imported}")
        defined = _defined_names(path)
        leaked = sorted(defined & PORT_FORBIDDEN_NAMES)
        if leaked:
            violations.append(f"{module} defines substrate names: {leaked}")
        # Also reject importing those names from elsewhere into port.
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name in PORT_FORBIDDEN_NAMES:
                        violations.append(f"{module} imports name {name}")
    assert violations == [], "AC4 violated:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# AC5 — consumers import the port, never adapter internals
# ---------------------------------------------------------------------------


def test_ac5_consumers_do_not_import_adapter_internals() -> None:
    violations: list[str] = []
    for module, path in _all_agentic_modules():
        if not any(_module_under(module, pkg) for pkg in PORT_CONSUMERS):
            continue
        for imported in _collect_imports(path):
            for prefix in ADAPTER_INTERNAL_PREFIXES:
                if _imports_match(imported, prefix):
                    violations.append(f"{module} imports adapter internal {imported}")
    assert violations == [], "AC5 violated:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# AC6 — binding never imports attestation minting APIs
# ---------------------------------------------------------------------------


def test_ac6_binding_does_not_import_attestation_minting() -> None:
    violations: list[str] = []
    for module, path in _all_agentic_modules():
        if not _module_under(module, "agentic.binding"):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.name
                    if name in ATTESTATION_MINTING_NAMES or name == "*":
                        # Star-import from trust.attestation would hide minting;
                        # forbid star imports from trust in binding.
                        if name == "*" and node.module and "attestation" in node.module:
                            violations.append(
                                f"{module} star-imports from {node.module}"
                            )
                        elif name in ATTESTATION_MINTING_NAMES:
                            violations.append(f"{module} imports minting API {name}")
            if isinstance(node, ast.Name) and node.id in ATTESTATION_MINTING_NAMES:
                violations.append(f"{module} references minting name {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in ATTESTATION_MINTING_NAMES:
                violations.append(f"{module} references minting attr {node.attr}")
    assert violations == [], "AC6 violated:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Skeleton shape — packages exist so later tasks plug in, not invent layout
# ---------------------------------------------------------------------------


REQUIRED_PACKAGES = (
    "agentic",
    "agentic.port",
    "agentic.adapters",
    "agentic.adapters.picn",
    "agentic.adapters.mock",
    "agentic.agentic_layer",
    "agentic.trust",
    "agentic.binding",
    "agentic.scenario",
    "agentic.benchmark",
    "agentic.tests",
)


def test_package_skeleton_exists() -> None:
    missing = [
        pkg
        for pkg in REQUIRED_PACKAGES
        if not (AGENTIC_ROOT.parent / Path(*pkg.split(".")) / "__init__.py").is_file()
    ]
    assert missing == [], f"Missing packages: {missing}"


@pytest.mark.parametrize("contract", ["AC1", "AC2", "AC3", "AC4", "AC5", "AC6"])
def test_contract_ids_are_documented(contract: str) -> None:
    """Sanity: each contract has a dedicated test function (no silent skips)."""
    assert any(
        name.startswith(f"test_{contract.lower()}_") for name in globals()
    ), f"No test function for {contract}"

# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment E runner CLI contract (design v4 §5.3).

Covers the flag surface, the cost preflight (``--max-hours`` / ``--ack-cost``
/ ``--dry-run``), backend toggle + TOML model-config handling, and namespace
isolation: the runner writes **only** ``kind="physical_run"`` records and never
touches the ``concurrency_run`` namespace.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import a submodule of agentic.agentic_layer before agentic.binding so the
# pre-existing registry<->layer circular import resolves in a stable order.
from agentic.agentic_layer.descriptor import CapabilityDescriptor  # noqa: F401
from demo import run_physical
from demo.physical_topology import PhysicalLeafOutcome, PhysicalRunResult, leaf_name_str
from demo.run_physical import main, parse_edge_endpoints


def _campaign_args(tmp_path: Path, **overrides: str) -> list[str]:
    args = [
        "--seeds", "1",
        "--k", "2",
        "--edges", "1",
        "--runs-per-cell", "1",
        "--leaf-latency-s", "0.01",
        "--allow-dirty",
        "--out", str(tmp_path / "physical.jsonl"),
    ]
    for key, value in overrides.items():
        if value is None:
            continue
        if value == "":  # store_true-style flag
            args.append(key)
        else:
            args.extend([key, value])
    return args


def _read_records(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# --- CLI contract ------------------------------------------------------------


def test_cli_flag_surface(tmp_path: Path) -> None:
    """Every documented flag parses; a dry-run with sane max-hours exits 0."""
    code = main(
        [
            "--backend", "deterministic",
            "--llm-placement", "on-pi",
            "--edges", "2",
            "--seeds", "1",
            "--k", "2",
            "--leaf-latency-s", "0.01",
            "--runs-per-cell", "1",
            "--model-config", "does-not-matter-for-deterministic.toml",
            "--transport", "udp",
            "--run-id", "flags",
            "--out", str(tmp_path / "flags.jsonl"),
            "--max-hours", "6",
            "--observer", "on",
            "--dry-run",
            "--ack-cost",
            "--allow-dirty",
        ]
    )
    assert code == 0


def test_cli_rejects_zero_edges() -> None:
    with pytest.raises(SystemExit):
        main(["--edges", "0", "--allow-dirty"])


def test_cli_runs_cold_no_import_cycle() -> None:
    """Cold-interpreter regression: ``python -m demo.run_physical`` must not
    crash with the registry<->agentic_layer import cycle (invoked here in a
    fresh subprocess with no pre-warmed import order)."""
    repo_root = Path(run_physical.__file__).resolve().parent.parent
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "demo.run_physical",
            "--backend", "llm",
            "--llm-placement", "on-pi",
            "--edges", "2",
            "--seeds", "1",
            "--k", "8",
            "--runs-per-cell", "1",
            "--dry-run",
            "--max-hours", "0",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
        timeout=60,
    )
    assert "ImportError" not in proc.stderr
    assert "Cost preflight" in proc.stdout
    assert proc.returncode == 3


# --- namespace isolation -----------------------------------------------------


def test_writes_only_physical_run_records(tmp_path: Path) -> None:
    out = tmp_path / "physical.jsonl"
    code = main(
        _campaign_args(tmp_path, **{"--transport": "udp", "--run-id": "ns-1"})
    )
    assert code == 0
    records = _read_records(out)
    assert len(records) >= 1
    assert all(r["kind"] == "physical_run" for r in records)
    assert all(r["metadata"]["transport"] == "udp" for r in records)
    # T_intent decomposition present per record.
    record = records[0]
    for component in (
        "t_decompose_ms",
        "t_dispatch_ms",
        "t_network_ms",
        "t_service_ms",
        "t_aggregate_ms",
        "t_intent_ms",
    ):
        assert component in record


def test_runner_never_touches_concurrency_namespace() -> None:
    """AST check: the runner's source must not use the concurrency_run kind."""
    source = Path(run_physical.__file__).read_text(encoding="utf-8")
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value in {"concurrency_run", "physical_run"}
    }
    assert literals == {"physical_run"}


# --- cost preflight ----------------------------------------------------------


def test_cost_preflight_refuses_over_max_hours(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--seeds", "1",
            "--k", "8",
            "--edges", "2",
            "--runs-per-cell", "30",
            "--backend", "llm",
            "--llm-placement", "on-pi",
            "--max-hours", "0",
            "--dry-run",
            "--allow-dirty",
            "--out", str(tmp_path / "never.jsonl"),
        ]
    )
    assert code == 3
    captured = capsys.readouterr()
    assert "Cost preflight" in captured.out


def test_llm_preflight_ack_cost_proceeds(tmp_path: Path) -> None:
    model_toml = tmp_path / "model.toml"
    model_toml.write_text(
        '[model]\npreference = ["test"]\n', encoding="utf-8"
    )
    code = main(
        _campaign_args(
            tmp_path,
            **{
                "--backend": "llm",
                "--llm-placement": "on-pi",
                "--model-config": str(model_toml),
                "--ack-cost": "",
                "--run-id": "llm-ack",
            },
        )
    )
    assert code == 0
    records = _read_records(tmp_path / "physical.jsonl")
    assert len(records) == 1  # 1 seed x 1 k x 1 run
    assert records[0]["backend"] == "llm"
    assert records[0]["llm_placement"] == "on-pi"
    # LLM split present per leaf; leaf latency dropped from LLM cells.
    for leaf in records[0]["leaves"]:
        assert leaf["inference_ms"] is not None
        assert leaf["transport_ms"] is not None
    assert records[0]["leaf_latency_s"] is None


def test_dry_run_writes_no_campaign_output(tmp_path: Path, capsys) -> None:
    out = tmp_path / "dry.jsonl"
    code = main(
        [
            "--seeds", "1",
            "--k", "2",
            "--edges", "1",
            "--runs-per-cell", "1",
            "--leaf-latency-s", "0.01",
            "--allow-dirty",
            "--dry-run",
            "--out", str(out),
        ]
    )
    assert code == 0
    assert "Cost preflight" in capsys.readouterr().out
    assert not out.exists()


# --- backend toggle + model config ------------------------------------------


def test_deterministic_backend_never_loads_model_config(tmp_path: Path) -> None:
    code = main(
        _campaign_args(
            tmp_path,
            **{
                "--model-config": str(tmp_path / "does-not-exist.toml"),
                "--run-id": "det-no-config",
            },
        )
    )
    assert code == 0


def test_llm_backend_missing_model_config_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            _campaign_args(
                tmp_path,
                **{
                    "--backend": "llm",
                    "--model-config": str(tmp_path / "does-not-exist.toml"),
                    "--ack-cost": "",
                },
            )
        )


# --- --edge-endpoints: targeting deployed (remote) bench edges ----------------


def _fake_physical_result() -> PhysicalRunResult:
    leaves = [
        PhysicalLeafOutcome(
            leaf_index=index,
            name=leaf_name_str(index),
            edge_id=f"edge{index + 1}",
            served_by_host="10.0.0.12",
            inference_ms=None,
            transport_ms=1.0,
            service_ms=None,
        )
        for index in range(2)
    ]
    return PhysicalRunResult(
        run_id="remote-1",
        seed=1,
        k=2,
        edges=2,
        backend="deterministic",
        llm_placement=None,
        leaf_latency_s=0.01,
        transport="udp",
        dispatch="concurrent",
        elapsed_ms=1.0,
        trace_root_hex="ab" * 32,
        trace_root_verified=True,
        t_decompose_ms=1.0,
        t_dispatch_ms=1.0,
        t_network_ms=1.0,
        t_service_ms=None,
        t_aggregate_ms=1.0,
        t_intent_ms=1.0,
        leaves=leaves,
        per_edge_leaf_counts={"edge1": 1, "edge2": 1},
        clock_offset_ms=0.0,
        hosts=["pi-01"],
    )


def test_parse_edge_endpoints_comma_and_repeated_form() -> None:
    expected = [("10.0.0.12", 9001), ("10.0.0.13", 9002)]
    assert parse_edge_endpoints(
        ["edge1=10.0.0.12:9001,edge2=10.0.0.13:9002"], 2
    ) == expected
    assert parse_edge_endpoints(
        ["edge2=10.0.0.13:9002", "edge1=10.0.0.12:9001"], 2
    ) == expected


def test_parse_edge_endpoints_empty_is_loopback() -> None:
    assert parse_edge_endpoints([], 2) is None
    assert parse_edge_endpoints([""], 2) is None


@pytest.mark.parametrize(
    "raw",
    [
        ["edge1=10.0.0.12"],  # no port
        ["edge1=:9001"],  # no host
        ["edge1=10.0.0.12:notaport"],  # non-integer port
        ["edge1=10.0.0.12:0"],  # port below range
        ["edge1=10.0.0.12:70000"],  # port above range
        ["edge1=10.0.0.12:9001,edge1=10.0.0.13:9002"],  # duplicate edge id
        ["edge3=10.0.0.14:9003"],  # unknown edge id
        ["host1=10.0.0.12:9001"],  # not an edgeN id
        ["edge1=10.0.0.12:9001"],  # incomplete coverage for --edges 2
    ],
)
def test_parse_edge_endpoints_rejects_malformed_input(raw: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_edge_endpoints(raw, 2)


def test_cli_rejects_malformed_edge_endpoints(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--edge-endpoints"):
        main(_campaign_args(tmp_path, **{"--edge-endpoints": "edge1=bogus"}))


def test_cli_plumbs_edge_endpoints_into_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict] = []

    async def stub(**kwargs: object) -> PhysicalRunResult:
        captured.append(kwargs)  # type: ignore[arg-type]
        return _fake_physical_result()

    monkeypatch.setattr(run_physical, "run_physical_cell", stub)
    code = main(
        _campaign_args(
            tmp_path,
            **{
                "--edges": "2",
                "--edge-endpoints": "edge1=10.0.0.12:9001,edge2=10.0.0.13:9002",
                "--intake-host": "pi-01",
                "--run-id": "remote-1",
            },
        )
    )
    assert code == 0
    # The parsed (host, port) list reaches the topology layer, ordered edge1..N.
    assert captured[0]["edge_endpoints"] == [
        ("10.0.0.12", 9001),
        ("10.0.0.13", 9002),
    ]
    records = _read_records(tmp_path / "physical.jsonl")
    assert records[0]["metadata"]["edge_endpoints"] == {
        "edge1": "10.0.0.12:9001",
        "edge2": "10.0.0.13:9002",
    }
    assert records[0]["metadata"]["intake_host"] == "pi-01"


def test_loopback_default_records_no_edge_endpoints(tmp_path: Path) -> None:
    out = tmp_path / "physical.jsonl"
    code = main(_campaign_args(tmp_path, **{"--run-id": "loopback-meta"}))
    assert code == 0
    record = _read_records(out)[0]
    assert "edge_endpoints" not in record["metadata"]
    assert "intake_host" not in record["metadata"]

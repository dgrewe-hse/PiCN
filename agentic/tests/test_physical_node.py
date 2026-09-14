# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Per-node entrypoint and service-record collection (Experiment E).

Covers the env contract (``PICN_ROLE`` … ``PICN_EDGES``), per-node run
markers, the systemd-friendly serving loop, intake delegation, and the
clock-offset correction applied by ``collect_service_records``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo import collect_service_records, physical_node

ENV_CONTRACT = (
    "PICN_ROLE",
    "PICN_EDGE_ID",
    "PICN_LISTEN_PORT",
    "PICN_PEER_HOST",
    "PICN_PEER_PORT",
    "PICN_OWNED_PREFIXES",
    "PICN_BACKEND",
    "PICN_LLM_PLACEMENT",
    "PICN_MODEL_CONFIG",
    "PICN_RESULTS_DIR",
    "PICN_RUN_ID",
    "PICN_SEED",
    "PICN_K",
    "PICN_LEAF_LATENCY_S",
    "PICN_EDGES",
)


def _edge_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {name: "" for name in ENV_CONTRACT}
    env.update(
        {
            "PICN_ROLE": "edge",
            "PICN_EDGE_ID": "edge1",
            "PICN_LISTEN_PORT": "0",
            "PICN_OWNED_PREFIXES": "/cap/fwd/hospital/beds/h1,/cap/fwd/hospital/beds/h2",
            "PICN_BACKEND": "deterministic",
            "PICN_RESULTS_DIR": str(tmp_path / "results"),
            "PICN_RUN_ID": "e2-det-1",
            "PICN_K": "8",
            "PICN_EDGES": "2",
            "PICN_LEAF_LATENCY_S": "0.05",
        }
    )
    env.update(overrides)
    return env


# --- env contract ------------------------------------------------------------


def test_env_contract_is_exactly_the_design_set() -> None:
    assert set(physical_node.ENV_KEYS) == set(ENV_CONTRACT)


def test_read_env_config_parses_contract(monkeypatch, tmp_path: Path) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    config = physical_node.read_env_config()
    assert config.role == "edge"
    assert config.edge_id == "edge1"
    assert config.listen_port == 0
    assert config.owned_prefixes == [
        "/cap/fwd/hospital/beds/h1",
        "/cap/fwd/hospital/beds/h2",
    ]
    assert config.k == 8
    assert config.edges == 2
    assert config.leaf_latency_s == 0.05


def test_read_env_config_requires_role(monkeypatch) -> None:
    for key in ENV_CONTRACT:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit):
        physical_node.read_env_config()


def test_read_env_config_rejects_unknown_role(monkeypatch, tmp_path: Path) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PICN_ROLE", "relay")
    with pytest.raises(SystemExit):
        physical_node.read_env_config()


# --- per-node run markers ----------------------------------------------------


def test_start_and_stop_write_run_marker(monkeypatch, tmp_path: Path) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    config = physical_node.read_env_config()
    handle = physical_node.write_start_marker(config, host="pi-02")
    marker_file = (
        tmp_path / "results" / "markers" / "e2-det-1-pi-02.json"
    )
    assert marker_file.is_file()
    marker = json.loads(marker_file.read_text(encoding="utf-8"))
    assert marker["host"] == "pi-02"
    assert marker["role"] == "edge"
    assert marker["deployed_commit"] == marker["disk_commit"]
    assert marker["owned_prefixes"][0].endswith("h1")
    assert marker["started_at"] and not marker["ended_at"]

    physical_node.write_end_marker(config, handle, host="pi-02")
    ended = json.loads(marker_file.read_text(encoding="utf-8"))
    assert ended["ended_at"]


# --- systemd-friendly serving loop ------------------------------------------


@pytest.mark.asyncio
async def test_serve_edge_runs_until_stopped(monkeypatch, tmp_path: Path) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    config = physical_node.read_env_config()
    handle = physical_node.write_start_marker(config, host="pi-02")
    stop = await physical_node.serve_edge(config, handle, host="pi-02", soak_s=0.05)
    assert stop is True
    ended = json.loads(
        (tmp_path / "results" / "markers" / "e2-det-1-pi-02.json").read_text(
            encoding="utf-8"
        )
    )
    assert ended["ended_at"]


# --- intake delegation -------------------------------------------------------


def test_intake_role_delegates_to_run_physical(monkeypatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_main(argv):
        captured.append(argv)
        return 0

    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PICN_ROLE", "intake")
    monkeypatch.setattr("demo.run_physical.main", fake_main)
    assert physical_node.main([]) == 0
    assert captured and "--edges" in captured[0] and "2" in captured[0]


def test_intake_argv_backend_llm_carries_placement_and_model(
    monkeypatch, tmp_path: Path
) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PICN_BACKEND", "llm")
    monkeypatch.setenv("PICN_LLM_PLACEMENT", "off-pi")
    monkeypatch.setenv("PICN_MODEL_CONFIG", "/tmp/model.toml")
    monkeypatch.delenv("PICN_LEAF_LATENCY_S", raising=False)
    config = physical_node.read_env_config()
    argv = physical_node._intake_argv(config)
    assert "--backend" in argv and "llm" in argv
    assert "--llm-placement" in argv and "off-pi" in argv
    assert "--model-config" in argv and "/tmp/model.toml" in argv
    # LLM cells drop --leaf-latency-s (Finding 9).
    assert "--leaf-latency-s" not in argv


def test_observer_role_writes_only_markers(monkeypatch, tmp_path: Path) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PICN_ROLE", "observer")
    monkeypatch.setenv("PICN_NODE_HOST", "pi-07")
    assert physical_node.main([]) == 0
    marker_file = tmp_path / "results" / "markers" / "e2-det-1-pi-07.json"
    ended = json.loads(marker_file.read_text(encoding="utf-8"))
    assert ended["role"] == "observer" and ended["ended_at"]


def test_serving_role_dispatches_to_serve_edge(monkeypatch, tmp_path: Path) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PICN_NODE_HOST", "pi-02")
    called: list[tuple[str, str]] = []

    async def fake_serve_edge(config, handle, *, host, soak_s=None):
        called.append((config.role, host))
        return True

    monkeypatch.setattr(physical_node, "serve_edge", fake_serve_edge)
    assert physical_node.main([]) == 0
    assert called == [("edge", "pi-02")]


@pytest.mark.asyncio
async def test_serve_edge_tolerates_wedged_stop(monkeypatch, tmp_path: Path) -> None:
    for key, value in _edge_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    config = physical_node.read_env_config()
    handle = physical_node.write_start_marker(config, host="pi-02")
    monkeypatch.setattr(
        "PiCN.ProgramLibs.AgenticForwarder.AgenticForwarder.stop_forwarder_async",
        _wedged_stop,
    )
    assert await physical_node.serve_edge(config, handle, host="pi-02", soak_s=0.05)
    ended = json.loads(
        (tmp_path / "results" / "markers" / "e2-det-1-pi-02.json").read_text(
            encoding="utf-8"
        )
    )
    assert ended["ended_at"]


async def _wedged_stop(self) -> None:
    raise RuntimeError("wedged layer stop")


# --- clock-offset correction -------------------------------------------------


def _service_record(run_id: str, leaf: int, measured_ms: float, offset_ms: float):
    return {
        "run_id": run_id,
        "leaf_index": leaf,
        "name": f"/cap/fwd/hospital/beds/h{leaf + 1}",
        "edge_id": "edge1",
        "host": "pi-02",
        "service_measured_ms": measured_ms,
        "clock_offset_ms": offset_ms,
    }


def test_collect_applies_clock_offset_correction(tmp_path: Path) -> None:
    records_dir = tmp_path / "results"
    srv = records_dir / "service_records"
    srv.mkdir(parents=True)
    (srv / "pi-02.json").write_text(
        json.dumps(
            [
                _service_record("r1", 0, 50.0, 1.5),
                _service_record("r1", 1, 60.0, 0.0),
            ]
        ),
        encoding="utf-8",
    )
    skew = collect_service_records.residual_skew_ms(
        [_service_record("r1", 0, 50.0, 1.5)]
    )
    assert skew == pytest.approx(1.5)

    out = tmp_path / "corrected.jsonl"
    code = collect_service_records.main(
        [
            "--results-dir", str(records_dir),
            "--out", str(out),
            "--skew-threshold-ms", "1.0",
        ]
    )
    assert code == 0
    corrected = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    by_leaf = {r["leaf_index"]: r for r in corrected}
    assert by_leaf[0]["service_corrected_ms"] == pytest.approx(48.5)
    assert by_leaf[1]["service_corrected_ms"] == pytest.approx(60.0)
    assert by_leaf[0]["skew_exceeded"] is True
    assert by_leaf[1]["skew_exceeded"] is False


def test_collect_merges_markers_into_run_records(tmp_path: Path) -> None:
    records_dir = tmp_path / "results"
    markers = records_dir / "markers"
    markers.mkdir(parents=True)
    marker = {
        "host": "pi-02",
        "role": "edge",
        "deployed_commit": "abc",
        "disk_commit": "abc",
        "owned_prefixes": ["/cap/fwd/hospital/beds/h1"],
        "started_at": "t0",
        "ended_at": "t1",
    }
    (markers / "e2-det-1-pi-02.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    jsonl = tmp_path / "physical.jsonl"
    run = {
        "kind": "physical_run",
        "record_type": "run",
        "run_id": "e2-det-1",
        "node_markers": [],
    }
    jsonl.write_text(json.dumps(run) + "\n", encoding="utf-8")

    code = collect_service_records.main(
        [
            "--results-dir", str(records_dir),
            "--run-id", "e2-det-1",
            "--merge-into", str(jsonl),
        ]
    )
    assert code == 0
    merged = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert merged["node_markers"] == [marker]

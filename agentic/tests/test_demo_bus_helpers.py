# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Unit coverage for demo.bus_topology helpers."""

from __future__ import annotations

from demo.bus_topology import nfn_combine_interest, unblock_sim_interfaces


def test_nfn_combine_interest_depth_cap() -> None:
    name2 = nfn_combine_interest(2)
    assert name2.components[-1] == b"NFN" or str(name2).endswith("NFN")
    name10 = nfn_combine_interest(10)
    # Fan-out capped at 4 nested combine args.
    assert "h3" in str(name10)
    assert "h4" not in str(name10) or "h9" not in str(name10)


def test_unblock_sim_interfaces_tolerates_missing() -> None:
    class _Iface:
        queue_from_bus = None

        def close(self) -> None:
            raise RuntimeError("already closed")

    class _Node:
        interfaces = [_Iface()]

    unblock_sim_interfaces(_Node(), object())

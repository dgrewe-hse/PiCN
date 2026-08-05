# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""PiCN adapter — the sole module that may import ``PiCN.*``.

Translates between the substrate-neutral port and the modernized PiCN
async stack. All other agentic packages must stay free of ``PiCN`` imports.
"""

from agentic.adapters.picn.port import PicnSubstratePort

__all__ = ["PicnSubstratePort"]

# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""AgenticForwarder — async NFN stack topped by ``AgenticLayer`` (A-002).

``runtime`` must be ``async``. Sync ProgramLibs cannot host the agentic layer.
"""

from __future__ import annotations

from typing import List, Optional, Union

from PiCN.LayerStack.AsyncLayerStack import AsyncLayerStack
from PiCN.Layers.ChunkLayer import AsyncBasicChunkLayer
from PiCN.Layers.ChunkLayer.Chunkifyer import SimpleContentChunkifyer
from PiCN.Layers.ICNLayer import AsyncBasicICNLayer
from PiCN.Layers.ICNLayer.ContentStore import ContentStoreMemoryExact
from PiCN.Layers.ICNLayer.ForwardingInformationBase import (
    ForwardingInformationBaseMemoryPrefix,
)
from PiCN.Layers.ICNLayer.PendingInterestTable import PendingInterstTableMemoryExact
from PiCN.Layers.LinkLayer import AsyncBasicLinkLayer
from PiCN.Layers.LinkLayer.FaceIDTable import FaceIDDict
from PiCN.Layers.LinkLayer.Interfaces import BaseInterface, UDP4Interface
from PiCN.Layers.NFNLayer.AsyncBasicNFNLayer import AsyncBasicNFNLayer
from PiCN.Layers.NFNLayer.NFNComputationTable import NFNComputationList
from PiCN.Layers.NFNLayer.NFNExecutor import BaseNFNExecutor, NFNPythonExecutor
from PiCN.Layers.NFNLayer.Parser import DefaultNFNParser
from PiCN.Layers.NFNLayer.R2C import TimeoutR2CHandler
from PiCN.Layers.PacketEncodingLayer import AsyncBasicPacketEncodingLayer
from PiCN.Layers.PacketEncodingLayer.Encoder import BasicEncoder, SimpleStringEncoder
from PiCN.Layers.TimeoutPreventionLayer import (
    AsyncBasicTimeoutPreventionLayer,
    TimeoutPreventionMessageDict,
)
from PiCN.Logger import Logger
from PiCN.Mgmt import AsyncMgmt
from PiCN.ProgramLibs.runtime import Runtime

from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.descriptor import CapabilityDescriptor
from agentic.agentic_layer.runtime import SyncRuntimeNotSupported
from agentic.binding.protocol import CapabilityBackend
from agentic.port.protocol import SubstratePort


class AgenticForwarder:
    """ICN/NFN forwarder with an agentic capability layer above NFN.

    :param port: UDP listen port (0 = ephemeral).
    :param log_level: PiCN logger level.
    :param encoder: Packet encoder.
    :param interfaces: Optional interfaces (UDP or SimulationBus faces).
    :param executors: NFN executors.
    :param ageing_interval: ICN ageing interval.
    :param runtime: Must be ``Runtime.ASYNC`` / ``\"async\"``.
    :param port_adapter: Optional ``SubstratePort`` attached to ``AgenticLayer``.
    """

    def __init__(
        self,
        port: int = 9000,
        log_level: int = 255,
        encoder: BasicEncoder | None = None,
        interfaces: List[BaseInterface] | None = None,
        executors: BaseNFNExecutor | dict | None = None,
        ageing_interval: int = 3,
        runtime: Union[Runtime, str] = Runtime.ASYNC,
        port_adapter: SubstratePort | None = None,
    ) -> None:
        if isinstance(runtime, str):
            runtime = Runtime(runtime)
        if runtime is not Runtime.ASYNC:
            raise SyncRuntimeNotSupported(
                "AgenticForwarder requires runtime='async' (A-002)"
            )
        self.runtime = runtime
        logger = Logger("AgenticForwarder", log_level)
        logger.info("Start PiCN Agentic Forwarder")

        self.encoder = encoder if encoder is not None else SimpleStringEncoder(
            log_level=log_level
        )
        if encoder is not None:
            encoder.set_log_level(log_level)

        self.parser = DefaultNFNParser()
        self.chunkifier = SimpleContentChunkifyer()
        self.r2cclient = TimeoutR2CHandler()
        if executors is None:
            self.executors: dict = {"PYTHON": NFNPythonExecutor()}
        elif isinstance(executors, dict):
            self.executors = executors
        else:
            self.executors = {"PYTHON": executors}

        cs = ContentStoreMemoryExact()
        fib = ForwardingInformationBaseMemoryPrefix()
        pit = PendingInterstTableMemoryExact()
        faceidtable = FaceIDDict()
        comp_table = NFNComputationList(self.r2cclient, self.parser)
        timeoutprevention_dict = TimeoutPreventionMessageDict()

        if interfaces is not None:
            self.interfaces = interfaces
            mgmt_port = port
        else:
            self.interfaces = [UDP4Interface(port)]
            mgmt_port = self.interfaces[0].get_port()

        self.linklayer = AsyncBasicLinkLayer(
            self.interfaces, faceidtable, log_level=log_level
        )
        self.packetencodinglayer = AsyncBasicPacketEncodingLayer(
            self.encoder, log_level=log_level
        )
        self.icnlayer = AsyncBasicICNLayer(
            log_level=log_level, ageing_interval=ageing_interval
        )
        self.chunklayer = AsyncBasicChunkLayer(self.chunkifier, log_level=log_level)
        self.icnlayer._interest_to_app = True
        self.nfnlayer = AsyncBasicNFNLayer(
            cs,
            fib,
            pit,
            faceidtable,
            comp_table,
            self.executors,
            self.parser,
            self.r2cclient,
            log_level=log_level,
        )
        self.timeoutpreventionlayer = AsyncBasicTimeoutPreventionLayer(
            timeoutprevention_dict, comp_table, pit=pit, log_level=log_level
        )
        self.agentic = AgenticLayer(
            log_level=log_level, runtime="async", port=port_adapter
        )
        self.lstack = AsyncLayerStack(
            [
                self.agentic,
                self.nfnlayer,
                self.chunklayer,
                self.timeoutpreventionlayer,
                self.icnlayer,
                self.packetencodinglayer,
                self.linklayer,
            ]
        )
        self.mgmt = AsyncMgmt(
            cs,
            fib,
            pit,
            self.linklayer,
            mgmt_port,
            self.stop_forwarder_async,
            log_level=log_level,
        )
        self.icnlayer.cs = cs
        self.icnlayer.fib = fib
        self.icnlayer.pit = pit
        self.cs = cs
        self.fib = fib
        self.pit = pit

    def register_capability(
        self,
        descriptor: CapabilityDescriptor,
        backend: CapabilityBackend,
        *,
        backend_label: str = "unspecified",
    ) -> None:
        """Plug a capability producer into the agentic layer."""
        self.agentic.register_capability(
            descriptor, backend, backend_label=backend_label
        )

    async def start_forwarder_async(self) -> None:
        self.lstack.start_all()
        if self.agentic.port is not None:
            await self.agentic.start_port()
        await self.mgmt.start()

    async def stop_forwarder_async(self) -> None:
        if self.agentic.port is not None:
            await self.agentic.stop_port()
        await self.mgmt.stop()
        await self.lstack.stop_all()


__all__ = ["AgenticForwarder"]

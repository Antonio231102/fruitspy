from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from typing import TypeAlias

Address: TypeAlias = tuple[str, int]


@dataclass(frozen=True, slots=True)
class DatagramDelivery:
    packet_index: int
    delay_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class DatagramFaultPlan:
    """Deliver selected packets in an explicit order with optional delays.

    Omitted indexes model loss, repeated indexes model duplication, and an order
    different from the source sequence models reordering.
    """

    deliveries: tuple[DatagramDelivery, ...]

    async def transmit(
        self,
        loop: asyncio.AbstractEventLoop,
        sock: socket.socket,
        destination: Address,
        packets: tuple[bytes, ...],
    ) -> None:
        for delivery in self.deliveries:
            if not 0 <= delivery.packet_index < len(packets):
                raise ValueError("fault-plan packet index is out of range")
            if delivery.delay_seconds < 0:
                raise ValueError("fault-plan delay must not be negative")
            if delivery.delay_seconds:
                await asyncio.sleep(delivery.delay_seconds)
            await loop.sock_sendto(
                sock,
                packets[delivery.packet_index],
                destination,
            )

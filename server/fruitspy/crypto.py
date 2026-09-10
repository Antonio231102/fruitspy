# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python adaptations of Luigi Auriemma's GameSpy cryptography, obtained through
# devzspy/GameSpy-Openspy-Core, revision 15ba5afe346447f95c1b097a45826b018cfe4a76:
# gsseckey: common/gsmsalg.cpp, Copyright 2004,2005,2006,2007,2008 Luigi Auriemma.
# PeerChatCipher: common/gs_peerchat.cpp/.h, Copyright 2004,2005,2006 Luigi Auriemma.
# EnctypeX: common/enctypex_decoder.cpp, Copyright 2008,2009 Luigi Auriemma.
# Original author/source: https://aluigi.altervista.org/
#
# Modified 2026-09-02: Python adaptations, byte/state handling, input validation,
# and supported-protocol scope. Attribution/license notices added 2026-09-10.
# These are modified implementations, not unmodified upstream files.
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later version.
# This program is distributed WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms and NOTICE for source mapping and attribution.
# If you did not receive the license, see https://www.gnu.org/licenses/.
#
from __future__ import annotations


_BASE64 = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def gsseckey(challenge: str, secret: str, enctype: int = 0) -> str:
    source = challenge.encode("ascii")
    key = secret.encode("ascii")
    if not 1 <= len(source) <= 65:
        return ""
    if not key:
        raise ValueError("secret cannot be empty")

    cards = list(range(256))
    acc = 0
    for index in range(256):
        acc = (acc + cards[index] + key[index % len(key)]) & 0xFF
        cards[index], cards[acc] = cards[acc], cards[index]

    acc = 0
    second = 0
    transformed = bytearray()
    for value in source:
        acc = (acc + value + 1) & 0xFF
        first_card = cards[acc]
        second = (second + first_card) & 0xFF
        second_card = cards[second]
        cards[second], cards[acc] = first_card, second_card
        transformed.append(value ^ cards[(first_card + second_card) & 0xFF])

    while len(transformed) % 3:
        transformed.append(0)
    if enctype == 2:
        for index in range(len(transformed)):
            transformed[index] ^= key[index % len(key)]
    elif enctype != 0:
        raise ValueError(f"unsupported QR enctype: {enctype}")

    output = bytearray()
    for index in range(0, len(transformed), 3):
        x, y, z = transformed[index : index + 3]
        output.extend(
            (
                _BASE64[x >> 2],
                _BASE64[((x & 3) << 4) | (y >> 4)],
                _BASE64[((y & 15) << 2) | (z >> 6)],
                _BASE64[z & 63],
            )
        )
    return output.decode("ascii")


class PeerChatCipher:
    def __init__(self, challenge: str, secret: str) -> None:
        challenge_bytes = challenge.encode("ascii")
        key = secret.encode("ascii")
        if len(challenge_bytes) != 16:
            raise ValueError("PeerChat challenge must be 16 bytes")
        if len(key) != 6:
            raise ValueError("PeerChat secret must be 6 bytes")

        mixed = bytes(
            challenge_bytes[index] ^ key[index % len(key)] for index in range(16)
        )
        cards = list(range(255, -1, -1))
        acc = 0
        for index in range(256):
            acc = (acc + mixed[index % 16] + cards[index]) & 0xFF
            cards[index], cards[acc] = cards[acc], cards[index]
        self._cards = cards
        self._first = 0
        self._second = 0

    def transform(self, data: bytes) -> bytes:
        output = bytearray(data)
        cards = self._cards
        first = self._first
        second = self._second
        for index, value in enumerate(output):
            first = (first + 1) & 0xFF
            card = cards[first]
            second = (second + card) & 0xFF
            cards[first], cards[second] = cards[second], card
            card = (card + cards[first]) & 0xFF
            output[index] = value ^ cards[card]
        self._first = first
        self._second = second
        return bytes(output)


class EnctypeX:
    def __init__(self, secret: str, validate: bytes, server_challenge: bytes) -> None:
        if len(validate) != 8:
            raise ValueError("Server Browser validation challenge must be 8 bytes")
        key = secret.encode("ascii")
        if not key:
            raise ValueError("secret cannot be empty")
        mixed = bytearray(validate)
        for index, value in enumerate(server_challenge):
            target = (key[index % len(key)] * index) % 8
            mixed[target] ^= mixed[index % 8] ^ value
        self._state = self._initialize(mixed)

    @staticmethod
    def _initialize(identity: bytes) -> list[int]:
        state = list(range(256)) + [0] * 5
        first = 0
        second = 0
        for count in range(255, -1, -1):
            selected, first, second = EnctypeX._select(
                state, count, identity, first, second
            )
            state[count], state[selected] = state[selected], state[count]
        state[256] = state[1]
        state[257] = state[3]
        state[258] = state[5]
        state[259] = state[7]
        state[260] = state[first & 0xFF]
        return state

    @staticmethod
    def _select(
        state: list[int],
        count: int,
        identity: bytes,
        first: int,
        second: int,
    ) -> tuple[int, int, int]:
        if count == 0:
            return 0, first, second
        mask = 1
        while mask < count:
            mask = (mask << 1) + 1
        attempts = 0
        while True:
            first = (state[first & 0xFF] + identity[second]) & 0xFFFFFFFF
            second += 1
            if second >= len(identity):
                second = 0
                first += len(identity)
            selected = first & mask
            attempts += 1
            if attempts > 11:
                selected %= count
            if selected <= count:
                return selected, first, second

    def encrypt(self, data: bytes) -> bytes:
        state = self._state
        output = bytearray(data)
        for index, value in enumerate(output):
            first = state[256]
            second = state[257]
            card = state[first]
            state[256] = (first + 1) & 0xFF
            state[257] = (second + card) & 0xFF
            first = state[260]
            second = state[state[257]]
            card = state[first]
            state[first] = second
            first = state[259]
            second = state[257]
            first = state[first]
            state[second] = first
            first = state[state[256]]
            second = state[259]
            state[second] = first
            state[state[256]] = card
            second = state[258]
            first = state[card]
            card = state[259]
            second = (second + first) & 0xFF
            state[258] = second
            first = state[second]
            card = (state[card] + state[state[257]] + state[state[260]]) & 0xFF
            second = state[card]
            card = state[state[256]]
            first = (first + card) & 0xFF
            card = state[second]
            second = state[first]
            encrypted = card ^ second ^ value
            state[260] = encrypted
            state[259] = value
            output[index] = encrypted
        return bytes(output)

    def decrypt(self, data: bytes) -> bytes:
        state = self._state
        output = bytearray(data)
        for index, value in enumerate(output):
            first = state[256]
            second = state[257]
            card = state[first]
            state[256] = (first + 1) & 0xFF
            state[257] = (second + card) & 0xFF
            first = state[260]
            second = state[state[257]]
            card = state[first]
            state[first] = second
            first = state[259]
            second = state[257]
            first = state[first]
            state[second] = first
            first = state[state[256]]
            second = state[259]
            state[second] = first
            state[state[256]] = card
            second = state[258]
            first = state[card]
            card = state[259]
            second = (second + first) & 0xFF
            state[258] = second
            first = state[second]
            card = (state[card] + state[state[257]] + state[state[260]]) & 0xFF
            second = state[card]
            card = state[state[256]]
            first = (first + card) & 0xFF
            card = state[second]
            second = state[first]
            plain = card ^ second ^ value
            state[260] = value
            state[259] = plain
            output[index] = plain
        return bytes(output)

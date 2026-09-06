from __future__ import annotations

import random
from collections.abc import Iterable, Iterator


def mutation_cases(
    seeds: Iterable[bytes],
    *,
    count: int,
    random_seed: int,
    max_size: int,
) -> Iterator[bytes]:
    """Yield a reproducible mix of structured mutations and arbitrary bytes."""
    corpus = tuple(bytes(seed) for seed in seeds)
    if not corpus:
        raise ValueError("at least one fuzz seed is required")
    if count < 1:
        raise ValueError("fuzz case count must be positive")
    if max_size < 1:
        raise ValueError("maximum fuzz input size must be positive")

    rng = random.Random(random_seed)
    boundary_sizes = (0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 20, 21, max_size - 1, max_size)

    for index in range(count):
        base = corpus[index % len(corpus)]
        operation = index % 9

        if operation == 0:
            size = max(0, min(max_size, boundary_sizes[(index // 9) % len(boundary_sizes)]))
            yield rng.randbytes(size)
            continue
        if operation == 1:
            size = rng.randrange(max_size + 1)
            yield rng.randbytes(size)
            continue
        if operation == 2:
            if not base:
                yield bytes((rng.randrange(256),))
                continue
            changed = bytearray(base[:max_size])
            offset = rng.randrange(len(changed))
            changed[offset] ^= 1 << rng.randrange(8)
            yield bytes(changed)
            continue
        if operation == 3:
            yield base[: rng.randrange(min(len(base), max_size) + 1)]
            continue
        if operation == 4:
            offset = rng.randrange(len(base) + 1)
            inserted = rng.randbytes(rng.randrange(1, min(33, max_size + 1)))
            yield (base[:offset] + inserted + base[offset:])[:max_size]
            continue
        if operation == 5:
            if not base:
                yield b""
                continue
            start = rng.randrange(len(base))
            stop = rng.randrange(start + 1, len(base) + 1)
            yield (base[:start] + base[stop:])[:max_size]
            continue
        if operation == 6:
            if not base:
                yield b""
                continue
            start = rng.randrange(len(base))
            stop = rng.randrange(start + 1, len(base) + 1)
            yield (base[:stop] + base[start:stop] + base[stop:])[:max_size]
            continue
        if operation == 7:
            value = base[index % len(base)] if base else rng.randrange(256)
            size = boundary_sizes[(index // 9) % len(boundary_sizes)]
            yield bytes((value,)) * max(0, min(max_size, size))
            continue

        other = corpus[rng.randrange(len(corpus))]
        left = rng.randrange(len(base) + 1)
        right = rng.randrange(len(other) + 1)
        yield (base[:left] + other[right:])[:max_size]

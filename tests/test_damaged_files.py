"""Damaged files: whatever the bytes, the readers raise FormatError and nothing else."""

import io
import random

import alphaquick as aq
from alphaquick import l2

from build import container, depth, l2_trade
from test_orderlog import capture, outcome_capture


def test_random_damage_only_ever_raises_format_errors():
    rng = random.Random(7)
    bases = [capture().getvalue(), outcome_capture().getvalue(),
             container(depth(1, 1.0, 1.0, 1) * 50 + l2_trade(2, 1.0, 1.0, 1) * 20)]
    readers = (
        lambda src: aq.Replay(src).run(),
        lambda src: [aq.L2Book().apply(e) for e in aq.read_l2(src) if type(e) is l2.DepthUpdate],
        aq.detect,
    )
    for _ in range(400):
        data = bytearray(rng.choice(bases))
        for _ in range(rng.randint(1, 8)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.3:
            data = data[:rng.randrange(len(data))]
        for read in readers:
            try:
                read(io.BytesIO(bytes(data)))
            except aq.FormatError:
                pass

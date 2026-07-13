"""Algorithm registry for continuous-position routing policies."""

from __future__ import annotations

import argparse

from simulator.src.partition import solve_dual_partition, solve_single_partition


ALGORITHM_NAMES = {
    "alg1": "partition baseline",
    "alg2": "dual partition baseline",
}


def solve_algorithm(name: str, args: argparse.Namespace, instance: dict) -> dict:
    if name == "alg1":
        return solve_single_partition(args, instance)
    if name == "alg2":
        return solve_dual_partition(args, instance)
    raise ValueError(f"Unsupported algorithm: {name}")

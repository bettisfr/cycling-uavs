"""Algorithm registry for continuous-position routing policies."""

from __future__ import annotations

import argparse

from simulator.src.dynamic_programming import solve_dynamic_programming_greedy
from simulator.src.greedy import solve_greedy_coverage
from simulator.src.partition import solve_partition
from simulator.src.trajectory_pool import solve_trajectory_pool


ALGORITHM_NAMES = {
    "alg1": "trajectory-pool marginal greedy",
    "alg2": "macro-action dynamic-programming greedy",
    "bs1": "fixed route-partition baseline",
    "bs2": "greedy target-chasing baseline",
}


def solve_algorithm(name: str, args: argparse.Namespace, instance: dict) -> dict:
    if name == "alg1":
        return solve_trajectory_pool(args, instance)
    if name == "alg2":
        return solve_dynamic_programming_greedy(args, instance)
    if name == "bs1":
        return solve_partition(args, instance, args.drones_per_segment)
    if name == "bs2":
        return solve_greedy_coverage(args, instance)
    raise ValueError(f"Unsupported algorithm: {name}")

#!/usr/bin/env python3
"""Run a cycling-UAV experiment from stage loading to optional map rendering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from simulator.src.algorithms import (
    ALGORITHM_NAMES,
    build_instance,
    choose_reference_gpx,
    repo_root,
    solve_algorithm,
)
from simulator.src.clustering import WEIGHT_POLICY, build_weighted_clusters
from simulator.src.preprocessing import build_stage_trace
from simulator.src.validation import check_feasible
from simulator.src.visualization import render_map


STATION_LAYOUTS_KM = {
    "dense": 5.0,
    "baseline": 7.5,
    "sparse": 10.0,
}

CHARGING_PROFILES_MIN = {
    "fast": 15.0,
    "baseline": 20.0,
    "slow": 25.0,
}


def default_trace_path(stage_id: str) -> Path:
    return (
        repo_root()
        / "simulator"
        / "output"
        / "traces"
        / f"{stage_id.upper()}_rider_points.parquet"
    )


def default_cluster_path(args: argparse.Namespace) -> Path:
    radius = f"{args.cluster_radius_m:g}".replace(".", "p")
    return (
        repo_root()
        / "simulator"
        / "output"
        / "clusters"
        / f"{args.stage_id.upper()}_{args.time_step_sec}s_r{radius}m_clusters.parquet"
    )


def default_output_json(args: argparse.Namespace) -> Path:
    tag = args.tag or (
        f"{args.stage_id.upper()}_{args.algorithm}_uav{args.num_uavs}_"
        f"{args.time_step_sec}s"
    )
    return repo_root() / "simulator" / "output" / "solutions" / f"{tag}.json"


def default_output_html(args: argparse.Namespace) -> Path:
    tag = args.tag or (
        f"{args.stage_id.upper()}_{args.algorithm}_uav{args.num_uavs}_"
        f"{args.time_step_sec}s"
    )
    return repo_root() / "simulator" / "output" / f"{tag}_map.html"


def ensure_trace(args: argparse.Namespace) -> Path:
    trace_parquet = args.trace_parquet or default_trace_path(args.stage_id)
    if trace_parquet.exists() and not args.preprocess:
        return trace_parquet

    preprocess_args = SimpleNamespace(
        stage_id=args.stage_id.upper(),
        competition_dir=args.competition_dir,
        output_dir=args.trace_output_dir,
        include_unlocked=args.include_unlocked,
        include_midnight=args.include_midnight,
        min_start_hhmm=args.min_start_hhmm,
    )
    summary = build_stage_trace(preprocess_args)
    built_path = Path(summary["parquet_path"])

    if args.trace_parquet and built_path.resolve() != trace_parquet.resolve():
        raise RuntimeError(
            f"Preprocessing wrote {built_path}, but --trace-parquet points to {trace_parquet}."
        )
    return built_path


def ensure_clusters(args: argparse.Namespace, trace_parquet: Path) -> Path:
    cluster_parquet = args.cluster_parquet or default_cluster_path(args)
    route_gpx, _rider_id = choose_reference_gpx(
        SimpleNamespace(stage_id=args.stage_id, reference_gpx=args.reference_gpx)
    )
    summary_path = cluster_parquet.with_name(
        f"{cluster_parquet.stem}_summary.json"
    )
    if (
        cluster_parquet.exists()
        and summary_path.exists()
        and not (args.preprocess or args.preprocess_clusters)
    ):
        metadata = json.loads(summary_path.read_text(encoding="utf-8"))
        expected = {
            "trace_parquet": str(trace_parquet),
            "route_gpx": str(route_gpx),
            "time_step_sec": args.time_step_sec,
            "cluster_radius_m": args.cluster_radius_m,
            "min_riders_per_bucket": args.min_riders_per_bucket,
            "weight_policy": WEIGHT_POLICY,
        }
        if all(metadata.get(key) == value for key, value in expected.items()):
            return cluster_parquet
    summary = build_weighted_clusters(
        stage_id=args.stage_id.upper(),
        trace_parquet=trace_parquet,
        route_gpx=route_gpx,
        output_dir=args.cluster_output_dir,
        time_step_sec=args.time_step_sec,
        cluster_radius_m=args.cluster_radius_m,
        min_riders_per_bucket=args.min_riders_per_bucket,
    )
    built_path = Path(summary["parquet_path"])
    if args.cluster_parquet and built_path.resolve() != cluster_parquet.resolve():
        raise RuntimeError(
            f"Cluster preprocessing wrote {built_path}, but --cluster-parquet "
            f"points to {cluster_parquet}."
        )
    return built_path


def make_solver_args(args: argparse.Namespace) -> SimpleNamespace:
    trace_parquet = ensure_trace(args)
    cluster_parquet = ensure_clusters(args, trace_parquet)

    return SimpleNamespace(
        stage_id=args.stage_id.upper(),
        trace_parquet=trace_parquet,
        cluster_parquet=cluster_parquet,
        num_uavs=args.num_uavs,
        num_stations=args.num_stations,
        station_spacing_m=(
            STATION_LAYOUTS_KM[args.station_layout] * 1000.0
            if args.algorithm in {"alg1", "alg2"}
            else None
        ),
        time_step_sec=args.time_step_sec,
        max_time_buckets=args.max_time_buckets,
        min_riders_per_bucket=args.min_riders_per_bucket,
        cluster_radius_m=args.cluster_radius_m,
        coverage_radius_m=args.coverage_radius_m,
        max_speed_mps=args.max_speed_mps,
        max_movement_m=args.max_movement_m,
        battery_capacity=args.battery_capacity,
        initial_battery=args.initial_battery,
        hover_energy_per_step=args.hover_energy_per_step,
        move_energy_per_meter=args.move_energy_per_meter,
        recharge_per_step=(
            args.recharge_per_step
            if args.recharge_per_step is not None
            else args.battery_capacity
            * args.time_step_sec
            / (CHARGING_PROFILES_MIN[args.charging_profile] * 60.0)
        ),
        recharge_threshold=args.recharge_threshold,
        station_recharge_bonus=args.station_recharge_bonus,
        reference_gpx=args.reference_gpx,
        free_start=args.free_start,
        allow_same_waypoint=args.allow_same_waypoint,
        station_capacity=args.station_capacity,
        time_limit_sec=args.time_limit_sec,
        verbose=args.verbose,
    )


def summarize_result(args: argparse.Namespace, result: dict, output_json: Path, output_html: Path | None) -> dict:
    total = result.get("total_cluster_weight") or 0
    objective = result.get("objective")
    return {
        "stage_id": args.stage_id.upper(),
        "algorithm": args.algorithm,
        "algorithm_name": ALGORITHM_NAMES[args.algorithm],
        "status_name": result.get("status_name"),
        "feasible": result.get("feasible"),
        "objective": objective,
        "total_cluster_weight": total,
        "coverage_ratio": objective / total if objective is not None and total else None,
        "time_buckets": len(result.get("time_buckets", [])),
        "time_step_sec": result.get("time_step_sec"),
        "output_json": str(output_json),
        "output_html": str(output_html) if output_html else None,
    }


def run_experiment(args: argparse.Namespace) -> dict:
    solver_args = make_solver_args(args)
    if args.only_preprocess:
        return {
            "stage_id": args.stage_id.upper(),
            "preprocessed_trace": str(solver_args.trace_parquet),
            "preprocessed_clusters": str(solver_args.cluster_parquet),
        }

    instance = build_instance(solver_args)

    result = solve_algorithm(args.algorithm, solver_args, instance)

    result["algorithm"] = args.algorithm
    result["algorithm_name"] = ALGORITHM_NAMES[args.algorithm]
    if args.algorithm in {"alg1", "alg2"}:
        result["feasible"] = check_feasible(solver_args, result)
        if not result["feasible"]:
            raise RuntimeError("alg1 produced an infeasible solution")

    output_json = args.output_json or default_output_json(args)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    output_html = None
    if args.render_map:
        output_html = args.output_html or default_output_html(args)
        render_args = SimpleNamespace(
            solution_json=output_json,
            output_html=output_html,
            max_route_points=args.max_route_points,
            full_stage_trace=solver_args.trace_parquet if args.full_stage_slider else None,
            time_step_sec=args.time_step_sec,
            min_riders_per_bucket=args.min_riders_per_bucket,
            cluster_radius_m=args.cluster_radius_m,
        )
        render_map(render_args)

    return summarize_result(args, result, output_json, output_html)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-id", default="S18")
    parser.add_argument("--algorithm", choices=sorted(ALGORITHM_NAMES), default="alg1")
    parser.add_argument("--tag", help="Output filename stem.")
    parser.add_argument("--trace-parquet", type=Path)
    parser.add_argument("--cluster-parquet", type=Path)
    parser.add_argument(
        "--reference-gpx",
        metavar="BIB",
        help="Rider bib used as the stage route reference (for example, B047).",
    )
    parser.add_argument("--preprocess", action="store_true")
    parser.add_argument("--preprocess-clusters", action="store_true")
    parser.add_argument("--only-preprocess", action="store_true")
    parser.add_argument(
        "--competition-dir",
        type=Path,
        default=repo_root() / "giro_2026",
    )
    parser.add_argument(
        "--trace-output-dir",
        type=Path,
        default=Path("simulator/output/traces"),
    )
    parser.add_argument(
        "--cluster-output-dir",
        type=Path,
        default=Path("simulator/output/clusters"),
    )
    parser.add_argument("--include-unlocked", action="store_true")
    parser.add_argument("--include-midnight", action="store_true")
    parser.add_argument("--min-start-hhmm", default="06:00")

    parser.add_argument("--num-uavs", type=int, default=6)
    parser.add_argument("--num-stations", type=int, default=25)
    parser.add_argument(
        "--station-layout",
        choices=sorted(STATION_LAYOUTS_KM),
        default="baseline",
        help="alg1 station spacing: dense=5 km, baseline=7.5 km, sparse=10 km.",
    )
    parser.add_argument("--time-step-sec", type=int, default=30)
    parser.add_argument("--max-time-buckets", type=int, default=10000)
    parser.add_argument("--min-riders-per-bucket", type=int, default=20)
    parser.add_argument("--cluster-radius-m", type=float, default=80.0)
    parser.add_argument("--coverage-radius-m", type=float, default=250.0)
    parser.add_argument("--max-speed-mps", type=float, default=120.0 / 3.6)
    parser.add_argument("--max-movement-m", type=float, default=300_000.0)

    parser.add_argument("--battery-capacity", type=float, default=10_000_000.0)
    parser.add_argument("--initial-battery", type=float, default=10_000_000.0)
    parser.add_argument("--hover-energy-per-step", type=float, default=50_000.0)
    parser.add_argument("--move-energy-per-meter", type=float, default=150.0)
    parser.add_argument(
        "--charging-profile",
        choices=sorted(CHARGING_PROFILES_MIN),
        default="fast",
        help="Full-charge time: fast=15 min, baseline=20 min, slow=25 min.",
    )
    parser.add_argument(
        "--recharge-per-step",
        type=float,
        help="Optional charging energy per slot in joules; overrides the profile.",
    )
    parser.add_argument("--recharge-threshold", type=float, default=3_750_000.0)
    parser.add_argument("--station-recharge-bonus", type=float, default=1000.0)

    parser.add_argument("--free-start", action="store_true")
    parser.add_argument("--allow-same-waypoint", action="store_true")
    parser.add_argument("--station-capacity", type=int, default=6)
    parser.add_argument("--time-limit-sec", type=float, default=60.0)

    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--render-map", action="store_true")
    parser.add_argument("--output-html", type=Path)
    parser.add_argument("--full-stage-slider", action="store_true")
    parser.add_argument("--max-route-points", type=int, default=3000)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    summary = run_experiment(args)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Aggregate solution artifacts and generate CSV tables and exploratory plots."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


METHOD_LABELS = {
    "alg1": "TPMG",
    "alg2": "MADP",
    "bs1_seg1": "BS1-S",
    "bs1_seg2": "BS1-D",
}
METHOD_ORDER = tuple(METHOD_LABELS.values())
METHOD_COLORS = {
    "TPMG": "#2878B5",
    "MADP": "#C74343",
    "BS1-S": "#D08A16",
    "BS1-D": "#2E7D5B",
}
ELECTRICITY_EUR_PER_KWH = 0.30
JET_A1_EUR_PER_LITER = 1.90
KING_AIR_B200_FUEL_BURN_US_GAL_PER_HOUR = 113.0
US_GALLON_LITERS = 3.78541
ENERGY_COST_METHODS = ("TPMG", "MADP")
ENERGY_COST_COLORS = {
    "TPMG": "#6B7B2A",
    "MADP": "#0F6B6B",
}


def _method_key(path: Path, payload: dict) -> str | None:
    stem = path.stem
    if payload.get("algorithm") in {"alg1", "alg2"}:
        return str(payload["algorithm"])
    if payload.get("algorithm") == "bs1":
        if "_seg1_" in stem:
            return "bs1_seg1"
        if "_seg2_" in stem:
            return "bs1_seg2"
    return None


def _flight_distance_km(placements: list[dict]) -> float:
    by_uav: dict[int, list[dict]] = defaultdict(list)
    for row in placements:
        by_uav[int(row["uav"])].append(row)

    distance_m = 0.0
    for rows in by_uav.values():
        rows.sort(key=lambda row: int(row["bucket"]))
        for previous, current in zip(rows, rows[1:]):
            if not current.get("airborne", False):
                continue
            latitude_scale = 111_000.0
            longitude_scale = latitude_scale * math.cos(
                math.radians((float(previous["lat"]) + float(current["lat"])) / 2.0)
            )
            delta_lat = (float(current["lat"]) - float(previous["lat"])) * latitude_scale
            delta_lon = (float(current["lon"]) - float(previous["lon"])) * longitude_scale
            distance_m += math.hypot(delta_lat, delta_lon)
    return distance_m / 1_000.0


def _placement_metrics(payload: dict) -> dict[str, float | int]:
    placements = payload.get("placements", [])
    by_uav: dict[int, list[dict]] = defaultdict(list)
    for row in placements:
        by_uav[int(row["uav"])].append(row)

    recharge_events = 0
    for rows in by_uav.values():
        rows.sort(key=lambda row: int(row["bucket"]))
        previously_recharging = False
        for row in rows:
            is_recharging = bool(row.get("recharging", False))
            recharge_events += int(is_recharging and not previously_recharging)
            previously_recharging = is_recharging

    return {
        "recharge_slots": sum(bool(row.get("recharging", False)) for row in placements),
        "recharge_events": recharge_events,
        "airborne_slots": sum(bool(row.get("airborne", False)) for row in placements),
        "flight_distance_km": _flight_distance_km(placements),
        "recovery_slots": int(payload.get("post_race_slots", 0)),
    }


def collect_stage_results(solution_dir: Path) -> list[dict]:
    """Load the four paper methods for the two evaluated fleet sizes."""
    rows: list[dict] = []
    for path in sorted(solution_dir.glob("S*_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        method_key = _method_key(path, payload)
        if method_key is None:
            continue

        stage_id = str(payload.get("stage_id") or path.stem.split("_", 1)[0]).upper()
        if not stage_id.startswith("S") or not stage_id[1:].isdigit():
            raise RuntimeError(f"Cannot determine the stage identifier for {path}.")
        stage_number = int(stage_id.removeprefix("S"))
        num_uavs = int(payload["num_uavs"])
        if num_uavs not in {4, 6} or stage_number == 10:
            continue

        objective = float(payload["objective"])
        total_weight = float(payload["total_cluster_weight"])
        rows.append(
            {
                "stage_id": stage_id,
                "stage_number": stage_number,
                "method": METHOD_LABELS[method_key],
                "num_uavs": num_uavs,
                "feasible": bool(payload["feasible"]),
                "objective": objective,
                "total_cluster_weight": total_weight,
                "coverage_ratio": objective / total_weight if total_weight else 0.0,
                **_placement_metrics(payload),
            }
        )

    expected = 20 * len(METHOD_LABELS) * 2
    if len(rows) != expected:
        raise RuntimeError(
            f"Expected {expected} paper results in {solution_dir}, found {len(rows)}."
        )
    return sorted(rows, key=lambda row: (row["stage_number"], row["num_uavs"], row["method"]))


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("Cannot write an empty CSV table.")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _uav_energy_cost_eur(row: dict) -> float:
    energy_kwh = (
        150.0 * float(row["flight_distance_km"])
        + 15.0 * float(row["airborne_slots"])
    ) / 3_600.0
    return energy_kwh * ELECTRICITY_EUR_PER_KWH


def _observed_flight_duration_hours(track_path: Path) -> float:
    with track_path.open(newline="", encoding="utf-8") as handle:
        timestamps = [int(row["timestamp_unix"]) for row in csv.DictReader(handle)]
    if len(timestamps) < 2:
        raise RuntimeError(f"Observed-flight track has fewer than two samples: {track_path}")
    return (max(timestamps) - min(timestamps)) / 3_600.0


def energy_cost_rows(rows: list[dict], flight_root: Path) -> list[dict]:
    """Create a stage-level energy-cost comparison with the observed B200 trace."""
    by_stage: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_stage[int(row["stage_number"])].append(row)

    fuel_cost_per_hour = (
        KING_AIR_B200_FUEL_BURN_US_GAL_PER_HOUR
        * US_GALLON_LITERS
        * JET_A1_EUR_PER_LITER
    )
    cost_rows: list[dict] = []
    for stage_number, stage_rows in sorted(by_stage.items()):
        stage_id = str(stage_rows[0]["stage_id"])
        track_path = flight_root / stage_id / "ASR251_track.csv"
        if not track_path.is_file():
            raise RuntimeError(f"Missing observed-flight track for {stage_id}: {track_path}")

        duration_hours = _observed_flight_duration_hours(track_path)
        cost_row: dict[str, float | int | str] = {
            "stage_id": stage_id,
            "stage_number": stage_number,
            "observed_aircraft_duration_hours": duration_hours,
            "observed_aircraft_cost_eur": duration_hours * fuel_cost_per_hour,
        }
        for method in ENERGY_COST_METHODS:
            for num_uavs in (4, 6):
                solution = next(
                    row
                    for row in stage_rows
                    if row["method"] == method and row["num_uavs"] == num_uavs
                )
                key = f"{method.lower()}_{num_uavs}_uav_cost_eur"
                cost_row[key] = _uav_energy_cost_eur(solution)
        cost_rows.append(cost_row)
    return cost_rows


def summarize_results(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(str(row["method"]), int(row["num_uavs"]))].append(row)

    summary: list[dict] = []
    for method in METHOD_ORDER:
        for num_uavs in (4, 6):
            values = groups[(method, num_uavs)]
            summary.append(
                {
                    "method": method,
                    "num_uavs": num_uavs,
                    "stages": len(values),
                    "mean_coverage_ratio": mean(float(row["coverage_ratio"]) for row in values),
                    "mean_objective": mean(float(row["objective"]) for row in values),
                    "mean_recharge_events": mean(float(row["recharge_events"]) for row in values),
                    "mean_recovery_slots": mean(float(row["recovery_slots"]) for row in values),
                    "mean_flight_distance_km": mean(float(row["flight_distance_km"]) for row in values),
                    "feasible_runs": sum(bool(row["feasible"]) for row in values),
                }
            )
    return summary


def _save_figure(figure: plt.Figure, output_dir: Path, stem: str) -> None:
    figure.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    figure.savefig(output_dir / f"{stem}.png", dpi=220, bbox_inches="tight")
    plt.close(figure)


def _plot_mean_coverage(summary: list[dict], output_dir: Path) -> None:
    figure, axis = plt.subplots(figsize=(7.2, 3.8))
    positions = list(range(len(METHOD_ORDER)))
    width = 0.34
    for offset, num_uavs in ((-width / 2.0, 4), (width / 2.0, 6)):
        values = [
            next(
                item["mean_coverage_ratio"]
                for item in summary
                if item["method"] == method and item["num_uavs"] == num_uavs
            )
            for method in METHOD_ORDER
        ]
        bars = axis.bar(
            [position + offset for position in positions],
            values,
            width=width,
            label=f"{num_uavs} UAVs",
            color=[METHOD_COLORS[method] for method in METHOD_ORDER],
            alpha=0.65 if num_uavs == 4 else 1.0,
            edgecolor="white",
        )
        axis.bar_label(bars, labels=[f"{100 * value:.1f}%" for value in values], padding=3, fontsize=8)
    axis.set_xticks(positions, METHOD_ORDER)
    axis.set_ylim(0.0, 1.05)
    axis.set_ylabel("Mean weighted coverage ratio")
    axis.legend(frameon=False, ncols=2, loc="upper left")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    _save_figure(figure, output_dir, "mean_coverage_ratio")


def _plot_coverage_by_stage(rows: list[dict], output_dir: Path) -> None:
    figure, axes = plt.subplots(2, 1, figsize=(8.2, 6.3), sharex=True, sharey=True)
    for axis, num_uavs in zip(axes, (4, 6), strict=True):
        for method in METHOD_ORDER:
            values = [
                row for row in rows if row["method"] == method and row["num_uavs"] == num_uavs
            ]
            axis.plot(
                [row["stage_number"] for row in values],
                [row["coverage_ratio"] for row in values],
                marker="o",
                markersize=3.5,
                linewidth=1.6,
                label=method,
                color=METHOD_COLORS[method],
            )
        axis.set_ylabel(f"Coverage ratio ({num_uavs} UAVs)")
        axis.set_ylim(0.0, 1.05)
        axis.grid(alpha=0.25)
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        frameon=False,
        ncols=4,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.01),
    )
    axes[1].set_xlabel("Giro 2026 road stage")
    axes[1].set_xticks([row["stage_number"] for row in rows if row["method"] == "TPMG" and row["num_uavs"] == 4])
    figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.94))
    _save_figure(figure, output_dir, "coverage_ratio_by_stage")


def _plot_coverage_distribution(rows: list[dict], output_dir: Path) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(8.2, 3.8), sharey=True)
    for axis, num_uavs in zip(axes, (4, 6), strict=True):
        values = [
            [
                row["coverage_ratio"]
                for row in rows
                if row["method"] == method and row["num_uavs"] == num_uavs
            ]
            for method in METHOD_ORDER
        ]
        boxplot = axis.boxplot(values, patch_artist=True, labels=METHOD_ORDER)
        for box, method in zip(boxplot["boxes"], METHOD_ORDER, strict=True):
            box.set_facecolor(METHOD_COLORS[method])
            box.set_alpha(0.72)
        axis.set_title(f"{num_uavs} UAVs")
        axis.grid(axis="y", alpha=0.25)
        axis.set_ylim(0.0, 1.05)
    axes[0].set_ylabel("Weighted coverage ratio")
    figure.tight_layout()
    _save_figure(figure, output_dir, "coverage_ratio_distribution")


def _plot_energy_cost_by_stage(cost_rows: list[dict], output_dir: Path) -> None:
    figure, axis = plt.subplots(figsize=(8.2, 3.9))
    stages = [int(row["stage_number"]) for row in cost_rows]
    axis.plot(
        stages,
        [float(row["observed_aircraft_cost_eur"]) for row in cost_rows],
        color="black",
        marker="o",
        markersize=3.5,
        linewidth=1.8,
        label="Observed B200",
    )
    for method in ENERGY_COST_METHODS:
        for num_uavs, linestyle in ((4, "-"), (6, "--")):
            key = f"{method.lower()}_{num_uavs}_uav_cost_eur"
            axis.plot(
                stages,
                [float(row[key]) for row in cost_rows],
                color=ENERGY_COST_COLORS[method],
                linestyle=linestyle,
                marker="o",
                markersize=3.5,
                linewidth=1.6,
                label=f"{method}, {num_uavs} UAVs",
            )
    axis.set_yscale("log")
    axis.set_xlabel("Giro 2026 road stage")
    axis.set_ylabel("Energy cost (EUR, log)")
    axis.set_xticks(stages)
    axis.grid(which="both", alpha=0.25)
    axis.legend(frameon=False, ncols=5, loc="upper center", bbox_to_anchor=(0.5, 1.24))
    figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.88))
    _save_figure(figure, output_dir, "energy_cost_by_stage")


def generate_results_artifacts(solution_dir: Path, output_dir: Path) -> dict:
    """Write TikZ-ready CSV tables and exploratory Matplotlib plots."""
    rows = collect_stage_results(solution_dir)
    summary = summarize_results(rows)
    flight_root = solution_dir.parents[2] / "giro_2026" / "flights"
    cost_rows = energy_cost_rows(rows, flight_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "stage_results.csv", rows)
    _write_csv(output_dir / "method_summary.csv", summary)
    _write_csv(output_dir / "energy_cost_by_stage.csv", cost_rows)
    _plot_mean_coverage(summary, output_dir)
    _plot_coverage_by_stage(rows, output_dir)
    _plot_coverage_distribution(rows, output_dir)
    _plot_energy_cost_by_stage(cost_rows, output_dir)
    return {
        "solution_dir": str(solution_dir),
        "plot_dir": str(output_dir),
        "stage_runs": len(rows),
        "csv_files": ["stage_results.csv", "method_summary.csv", "energy_cost_by_stage.csv"],
        "plot_stems": [
            "mean_coverage_ratio",
            "coverage_ratio_by_stage",
            "coverage_ratio_distribution",
            "energy_cost_by_stage",
        ],
    }

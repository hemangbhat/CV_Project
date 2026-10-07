"""Tests for the closed-loop virtual camera (``sim/sensor.py``) and, when SUMO is
installed, one end-to-end closed-loop run."""

from __future__ import annotations

import importlib.util
import random

import pytest

from sim.sensor import NoiseModel, SensorGeometry, VehicleObs, measure_approach, observe

GEOM = SensorGeometry(roi_length=150.0, queue_length_m=20.0, saturation_count=40.0, queue_capacity=4.0)
PCE = {"car": 1.0, "truck": 3.0, "bus": 3.0, "motorcycle": 0.5}


def measure(vehicles, gap=0.25):
    return measure_approach("North", vehicles, GEOM, pce_weights=PCE, use_pce_weighting=True,
                            stopped_speed_ratio=0.2, queue_tail_gap=gap)


def car(distance, speed=0.0):
    return VehicleObs(distance, speed, 5.0, "car")


def test_empty_approach_measures_zero() -> None:
    m = measure([])
    assert (m.vehicle_count, m.vehicle_density, m.normalized_queue, m.queue_reach) == (0, 0.0, 0.0, 0.0)


def test_queue_region_count_saturates_while_reach_keeps_growing() -> None:
    """The project's thesis, in the simulated sensor: once the Queue_Region is full,
    adding vehicles to the back of the queue no longer moves Q but still moves X."""
    short = measure([car(d) for d in (2, 9, 2, 9, 16, 16)])          # 6 queued, 2 lanes
    longer = measure([car(d) for d in (2, 9, 2, 9, 16, 16, 30, 37, 45, 52)])
    assert short.normalized_queue == longer.normalized_queue == 1.0
    assert longer.queue_reach > short.queue_reach


def test_moving_vehicles_do_not_extend_reach() -> None:
    m = measure([car(3), car(60, speed=10.0)])
    assert m.queue_reach == pytest.approx(3 / 150)


def test_pce_weights_density_not_queue() -> None:
    m = measure([VehicleObs(5.0, 0.0, 12.0, "truck")])
    assert m.vehicle_density == pytest.approx(3 / 40)
    assert m.queue_length == 1


def test_vehicles_beyond_the_roi_are_invisible() -> None:
    assert measure([car(200.0)]).vehicle_count == 0


def test_noise_drops_far_vehicles_more_than_near_ones() -> None:
    rng = random.Random(0)
    noise = NoiseModel(far_miss=0.5)
    near = sum(len(observe([car(1.0)], GEOM, noise, rng)) for _ in range(2000))
    far = sum(len(observe([car(149.0)], GEOM, noise, rng)) for _ in range(2000))
    assert near > 1950 and 900 < far < 1100


@pytest.mark.skipif(importlib.util.find_spec("libsumo") is None, reason="SUMO not installed")
def test_closed_loop_run_completes(tmp_path) -> None:
    from sim.closed_loop import ARMS, run
    from sim.scenario import Scenario

    tiny = Scenario("tiny", "smoke test", {a: 300.0 for a in ("North", "East", "South", "West")},
                    duration=240.0, warmup=30.0)
    result = run(tiny, ARMS["S4"], 1, timing="actuated", workdir=tmp_path)
    assert result.vehicles > 0
    assert result.mean_delay > 0.0
    assert result.greens >= 4

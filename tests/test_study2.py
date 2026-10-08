"""Unit tests for the study-2 policies (no SUMO needed)."""

import pytest

from sim.study2 import (
    BETA, GEOMETRIES, LEVELS, MAX_GREEN, METHODS, MIN_GREEN, PROTECT_AFTER, SHARES,
    Policy, barrier, condition, condition_names,
)

APPROACHES = ("North", "East", "South", "West")


def obs(count=None, qregion=None, risk=None, density=None, pressure=None):
    zero = {a: 0.0 for a in APPROACHES}
    return {
        "count": count or {a: 5 for a in APPROACHES},
        "queue_region": qregion or {a: 1 for a in APPROACHES},
        "risk": risk or dict(zero),
        "density": density or dict(zero),
        "pressure": pressure or dict(zero),
        "raza_scores": density or dict(zero),
    }


def test_barrier_is_zero_when_empty_and_grows_towards_storage_end():
    assert barrier(0.0, 1.1) == pytest.approx(0.0)
    values = [barrier(s, 1.1) for s in (0.0, 0.3, 0.6, 0.9, 1.0)]
    assert values == sorted(values)
    assert values[-1] > 8 * values[2]          # reciprocal: steep near the end of storage
    assert barrier(1.5, 1.1) == barrier(1.0, 1.1)  # capped at full storage


def test_conditions_cover_geometries_and_levels():
    names = condition_names()
    assert len(names) == len(GEOMETRIES) * len(LEVELS)
    scenario, lengths = condition("short_minor_3000")
    assert lengths["East"] == 60.0 and lengths["North"] == 150.0
    assert sum(scenario.demand.values()) == pytest.approx(3000.0, abs=1)
    assert scenario.demand["North"] == pytest.approx(3000 * SHARES["North"], abs=0.1)


def test_frozen_settings_match_protocol():
    prop = METHODS["PROP"]
    assert prop.protect and prop.beta == BETA == 0.85 and prop.lam == 1.0
    assert prop.protect_after == PROTECT_AFTER == 20.0
    assert METHODS["PROP_CNT"].spatial == "count"
    assert not METHODS["PROP_B"].protect


def test_fixed_time_round_robin_and_30s():
    p = Policy(METHODS["FT"], GEOMETRIES["uniform"])
    assert [p.choose(obs(), None) for _ in range(5)] == ["North", "East", "South", "West", "North"]
    assert not p.should_end("North", 29.5, 29.5, obs())
    assert p.should_end("North", 30.0, 30.0, obs())


def test_actuated_skips_empty_approaches():
    p = Policy(METHODS["ACT"], GEOMETRIES["uniform"])
    counts = {"North": 0, "East": 3, "South": 0, "West": 2}
    assert p.choose(obs(count=counts), None) == "East"
    assert p.choose(obs(count=counts), "East") == "West"


def test_actuated_gap_out_needs_min_green_and_passage_time():
    p = Policy(METHODS["ACT"], GEOMETRIES["uniform"])
    empty = obs(qregion={a: 0 for a in APPROACHES})
    p.on_green()
    assert not p.should_end("North", 3.0, 3.0, obs())      # occupied
    assert not p.should_end("North", 9.0, 9.0, empty)      # empty since 9 s, before min green
    assert not p.should_end("North", 10.0, 10.0, empty)    # 1 s empty < 2 s passage time
    assert p.should_end("North", 11.0, 11.0, empty)        # 2 s empty, past min green
    assert p.should_end("North", MAX_GREEN, MAX_GREEN, obs())  # max-out


def test_protection_waits_for_guard_then_cuts_for_a_filling_road():
    p = Policy(METHODS["PROP"], GEOMETRIES["short_minor"])
    p.on_green()
    risky = obs(risk={"North": 0.2, "East": 0.9, "South": 0.1, "West": 0.0})
    assert not p.should_end("North", MIN_GREEN + 1, MIN_GREEN + 1, risky)   # guard: 20 s
    assert p.should_end("North", PROTECT_AFTER, PROTECT_AFTER, risky)
    # a waiting road below beta, or no fuller than the active one, does not cut the green
    calm = obs(risk={"North": 0.95, "East": 0.9, "South": 0.1, "West": 0.0})
    assert not p.should_end("North", PROTECT_AFTER, PROTECT_AFTER, calm)


def test_barrier_only_never_cuts_for_storage():
    p = Policy(METHODS["PROP_B"], GEOMETRIES["short_minor"])
    p.on_green()
    risky = obs(risk={"North": 0.2, "East": 1.0, "South": 0.1, "West": 0.0})
    assert not p.should_end("North", 30.0, 30.0, risky)


def test_proposed_selection_prefers_a_nearly_full_short_road_over_a_denser_long_one():
    p = Policy(METHODS["PROP"], GEOMETRIES["short_minor"])
    density = {"North": 0.5, "East": 0.3, "South": 0.1, "West": 0.1}
    risk = {"North": 0.4, "East": 0.95, "South": 0.1, "West": 0.1}
    assert p.choose(obs(density=density, risk=risk), "West") == "East"
    # with no storage risk the choice is Raza's density argmax
    p2 = Policy(METHODS["PROP"], GEOMETRIES["short_minor"])
    assert p2.choose(obs(density=density), "West") == "North"


def test_cmp_switches_only_on_decision_steps_to_a_higher_pressure():
    p = Policy(METHODS["CMP"], GEOMETRIES["uniform"])
    pressure = {"North": 0.2, "East": 0.6, "South": 0.1, "West": 0.1}
    o = obs(pressure=pressure)
    assert not p.should_end("North", 9.5, 9.5, o)     # before min green
    assert p.should_end("North", 10.0, 10.0, o)       # decision step, East higher
    assert not p.should_end("North", 12.0, 12.0, o)   # not a decision step
    assert not p.should_end("East", 15.0, 15.0, o)    # active already the highest

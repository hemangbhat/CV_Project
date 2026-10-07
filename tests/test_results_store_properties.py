"""Property-based test for the Run_Log round trip (Property 19).

Requirement 14.4: for all Run_Logs, reading back a written log reproduces the
configuration values and the Evaluation_Metrics that were written, within 0.001 for
values represented as decimals.

The generated logs carry the awkward values a hand-written example would not think
to include: Alphas at both endpoints, per-frame records whose Scores are drawn from
the full unit range, metric values spanning several orders of magnitude, a
``processing_fps`` that is legitimately absent for a fixed-time run, and Track_ID
keys that JSON must represent as strings and this project must read back as numbers.

The tolerance is asserted at 0.001 because that is what the requirement promises,
and equality is asserted alongside it because JSON float repr in fact round-trips
exactly. If a future change to serialization loses precision, the equality check
fails while the tolerance check still passes — which is the difference between a
regression and a broken promise.

Validates: Requirements 14.4
Properties: Property 19
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.config import APPROACH_NAMES, load_config, to_json_obj
from src.results_store import ResultsStore, RunLog, make_run_id
from src.traffic_metrics import SIGNAL_STATES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = load_config(str(PROJECT_ROOT / "config" / "default.json"))

TOLERANCE = 0.001

_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_measure = st.floats(
    min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False, width=64
)
_counts = st.integers(min_value=0, max_value=500)


@st.composite
def _frame_records(draw: Any, frame_index: int, frame_rate: float) -> dict[str, Any]:
    """One per-frame record of the shape :func:`~src.results_store.frame_record` writes."""
    approaches: dict[str, Any] = {}
    for name in APPROACH_NAMES:
        count = draw(_counts)
        approaches[name] = {
            "vehicle_count": count,
            "queue_length": draw(st.integers(min_value=0, max_value=count)),
            "vehicle_density": draw(_unit),
            "normalized_queue": draw(_unit),
            "score": draw(_unit),
            "signal_state": draw(st.sampled_from(SIGNAL_STATES)),
        }
    return {
        "frame_index": frame_index,
        "simulated_time": frame_index / frame_rate,
        "approaches": approaches,
    }


@st.composite
def _evaluation_metrics(draw: Any, *, adaptive: bool) -> dict[str, Any]:
    """Per-Approach and aggregate metrics, as the Evaluation_Harness records them."""

    def one() -> dict[str, Any]:
        return {
            "avg_waiting_time": draw(_measure),
            "avg_queue_length": draw(_measure),
            "max_queue_length": draw(_counts),
            "vehicles_served": draw(_counts),
            "throughput": draw(_measure),
            # Requirement 13.3 records this for adaptive runs only, so ``None`` is
            # part of the generated space rather than a value to substitute.
            "processing_fps": draw(_measure) if adaptive else None,
        }

    return {
        "approaches": {name: one() for name in APPROACH_NAMES},
        "aggregate": one(),
    }


@st.composite
def _run_logs(draw: Any) -> RunLog:
    """A complete Run_Log with generated configuration and measurement values."""
    adaptive = draw(st.booleans())
    controller = "adaptive" if adaptive else "fixed"
    alpha = draw(st.one_of(st.just(0.0), st.just(1.0), _unit))
    frame_rate = draw(st.floats(min_value=1.0, max_value=120.0))
    frame_count = draw(st.integers(min_value=0, max_value=6))

    config = dataclasses.replace(
        DEFAULT_CONFIG,
        alpha=alpha,
        confidence_threshold=draw(_unit),
        starvation_limit=draw(st.integers(min_value=1, max_value=9)),
        yellow_duration=draw(st.floats(min_value=0.5, max_value=6.0)),
    )
    config_obj = to_json_obj(config)

    return RunLog(
        run_id=make_run_id("videos/generated clip.mp4", controller, alpha),
        video_path="videos/generated clip.mp4",
        controller_name=controller,
        alpha=alpha,
        config=config_obj,
        video_info={
            "path": "videos/generated clip.mp4",
            "width": draw(st.integers(min_value=16, max_value=4096)),
            "height": draw(st.integers(min_value=16, max_value=4096)),
            "frame_count": frame_count,
            "frame_rate": frame_rate,
            "frame_rate_substituted": draw(st.booleans()),
        },
        warnings=draw(st.lists(st.text(min_size=1, max_size=20), max_size=2)),
        overlap_events=[],
        starvation_overrides=[],
        phases=[],
        frames=[
            draw(_frame_records(index, frame_rate)) for index in range(frame_count)
        ],
        waiting_times={
            str(track_id): draw(_measure)
            for track_id in draw(
                st.lists(st.integers(min_value=1, max_value=999), max_size=5, unique=True)
            )
        },
        evaluation_metrics=draw(_evaluation_metrics(adaptive=adaptive)),
        complete=draw(st.booleans()),
    )


def _assert_close(written: Any, read: Any, where: str) -> None:
    """Assert two JSON values agree, decimals within :data:`TOLERANCE`."""
    if isinstance(written, bool) or written is None:
        assert read == written, f"{where}: {read!r} != {written!r}"
    elif isinstance(written, float):
        assert abs(read - written) <= TOLERANCE, f"{where}: {read!r} differs from {written!r}"
        assert read == written, f"{where}: {read!r} is not the exact value {written!r}"
    elif isinstance(written, int):
        assert read == written, f"{where}: {read!r} != {written!r}"
    elif isinstance(written, dict):
        assert set(read) == set(written), f"{where}: keys differ"
        for key in written:
            _assert_close(written[key], read[key], f"{where}.{key}")
    elif isinstance(written, (list, tuple)):
        assert len(read) == len(written), f"{where}: length differs"
        for index, value in enumerate(written):
            _assert_close(value, read[index], f"{where}[{index}]")
    else:
        assert read == written, f"{where}: {read!r} != {written!r}"


@settings(
    max_examples=40,
    deadline=None,
    # Each example writes and rereads a file, which is the point of the property.
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(log=_run_logs())
def test_property_19_run_logs_round_trip(log: RunLog, tmp_path_factory: Any) -> None:
    """Writing then reading a Run_Log reproduces every value it carried."""
    store = ResultsStore(str(tmp_path_factory.mktemp("run_logs")))

    path = store.write(log, store.default_path(log))
    reread = store.read(path)

    # Requirement 14.4 names configuration values and Evaluation_Metrics explicitly.
    _assert_close(log.config, reread.config, "config")
    _assert_close(log.evaluation_metrics, reread.evaluation_metrics, "evaluation_metrics")
    # The rest of the log is checked too: a log that lost its frame records while
    # preserving its metrics would satisfy the requirement and still be useless.
    _assert_close(log.as_dict(), reread.as_dict(), "run_log")
    assert reread.frame_count == log.frame_count
    assert reread.complete is log.complete
    assert reread.approach_names == log.approach_names


@settings(max_examples=40, deadline=None)
@given(log=_run_logs())
def test_property_19_in_memory_round_trip_needs_no_file(log: RunLog) -> None:
    """``as_dict`` and ``from_dict`` are inverses, independently of the filesystem."""
    rebuilt = RunLog.from_dict(log.as_dict())

    _assert_close(log.as_dict(), rebuilt.as_dict(), "run_log")
    assert rebuilt.simulated_duration == log.simulated_duration

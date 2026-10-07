"""Unit tests for the ROI coverage check (viewpoint mismatch).

The check answers one question about a candidate video: does every configured ROI
still cover road that traffic uses under this camera viewpoint? Tests drive it
with the scripted fakes over a synthetic clip, so no weights, GPU, or footage are
needed, and the tracks that reach the assigner are an exact known quantity.

Validates: Requirements 16.5
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config import APPROACH_NAMES, ApproachConfig, Config, load_config
from src.detection import Detection
from src.lane_analysis import (
    check_roi_coverage,
    coverage_sample_stride,
    roi_coverage_report,
)
from tests.fixtures import FakeDetector, FakeTracker, write_synthetic_clip

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

CLIP_WIDTH = 64
CLIP_HEIGHT = 48
CLIP_FRAME_COUNT = 8

#: One reference point per Approach, each inside that Approach's quadrant ROI.
QUADRANT_REFS: dict[str, tuple[int, int]] = {
    "North": (10, 10),
    "East": (50, 10),
    "South": (10, 30),
    "West": (50, 30),
}


def _approach(
    name: str,
    roi: tuple[tuple[int, int], ...],
    queue: tuple[tuple[int, int], ...],
) -> ApproachConfig:
    return ApproachConfig(
        name=name,
        roi_polygon=roi,
        queue_region=queue,
        saturation_count=4.0,
        queue_capacity=2.0,
    )


def _rect(x1: int, y1: int, x2: int, y2: int) -> tuple[tuple[int, int], ...]:
    return ((x1, y1), (x2, y1), (x2, y2), (x1, y2))


@pytest.fixture
def clip_config() -> Config:
    """The default config with four quadrant ROIs tiling a 64x48 frame.

    Quadrants rather than the placeholder geometry of ``config/default.json``,
    because the check has to be driven with reference points that land in a known
    Approach and the synthetic clip is 64x48.
    """
    config = load_config(str(DEFAULT_CONFIG_PATH))
    return dataclasses.replace(
        config,
        frame_size=(CLIP_WIDTH, CLIP_HEIGHT),
        approaches=(
            _approach("North", _rect(0, 0, 31, 23), _rect(2, 2, 29, 10)),
            _approach("East", _rect(32, 0, 63, 23), _rect(34, 2, 61, 10)),
            _approach("South", _rect(0, 24, 31, 47), _rect(2, 26, 29, 34)),
            _approach("West", _rect(32, 24, 63, 47), _rect(34, 26, 61, 34)),
        ),
    )


@pytest.fixture
def gap_config(clip_config: Config) -> Config:
    """The same frame with ROIs shrunk to the corners, leaving an uncovered middle.

    A stale viewpoint shows up as tracks driving through road no ROI covers; this
    config makes that state reachable, which the tiling quadrants cannot.
    """
    return dataclasses.replace(
        clip_config,
        approaches=(
            _approach("North", _rect(0, 0, 15, 11), _rect(2, 2, 13, 9)),
            _approach("East", _rect(48, 0, 63, 11), _rect(50, 2, 61, 9)),
            _approach("South", _rect(0, 36, 15, 47), _rect(2, 38, 13, 45)),
            _approach("West", _rect(48, 36, 63, 47), _rect(50, 38, 61, 45)),
        ),
    )


@pytest.fixture
def clip_path(tmp_path: Path) -> str:
    return str(
        write_synthetic_clip(
            tmp_path / "clip.mp4",
            frame_count=CLIP_FRAME_COUNT,
            width=CLIP_WIDTH,
            height=CLIP_HEIGHT,
        )
    )


def _script(
    refs: list[tuple[int, int]], frame_count: int = CLIP_FRAME_COUNT
) -> list[list[Detection]]:
    """Return a detection script placing one static box at each reference point.

    Boxes are built backwards from the bottom-edge midpoint the assigner tests, so
    each test names the geometry it cares about rather than a box whose midpoint
    the reader has to compute.
    """
    return [
        [
            Detection(
                x1=cx - 2,
                y1=cy - 6,
                x2=cx + 2,
                y2=cy,
                vehicle_class="car",
                confidence=0.9,
            )
            for cx, cy in refs
        ]
        for _ in range(frame_count)
    ]


def _fakes(script: list[list[Detection]]) -> tuple[FakeDetector, FakeTracker]:
    return FakeDetector(script), FakeTracker()


# ---------------------------------------------------------------------------
# Requirement 16.5 — matching configuration
# ---------------------------------------------------------------------------


def test_every_roi_covered_reports_no_mismatch(
    clip_path: str, clip_config: Config
) -> None:
    """Validates: Requirements 16.5"""
    detector, tracker = _fakes(_script(list(QUADRANT_REFS.values())))

    uncovered = check_roi_coverage(
        clip_path, clip_config, detector=detector, tracker=tracker
    )

    assert uncovered == []


def test_matching_video_report_counts_every_approach(
    clip_path: str, clip_config: Config
) -> None:
    """A matching video leaves no mismatch message to report (Req 16.5)."""
    detector, tracker = _fakes(_script(list(QUADRANT_REFS.values())))

    report = roi_coverage_report(
        clip_path, clip_config, detector=detector, tracker=tracker
    )

    assert report.frames_sampled == CLIP_FRAME_COUNT
    assert report.matches is True
    assert report.mismatch_message() is None
    assert report.unassigned == 0
    assert all(report.observations[name] == CLIP_FRAME_COUNT for name in APPROACH_NAMES)


# ---------------------------------------------------------------------------
# Requirement 16.5 — mismatched configuration
# ---------------------------------------------------------------------------


def test_roi_with_no_assigned_track_is_reported(
    clip_path: str, clip_config: Config
) -> None:
    """Validates: Requirements 16.5"""
    refs = [ref for name, ref in QUADRANT_REFS.items() if name != "West"]
    detector, tracker = _fakes(_script(refs))

    uncovered = check_roi_coverage(
        clip_path, clip_config, detector=detector, tracker=tracker
    )

    assert uncovered == ["West"]


def test_mismatch_message_names_the_video_and_the_uncovered_approaches(
    clip_path: str, clip_config: Config
) -> None:
    """The caller excludes the video, so the report has to say which and why."""
    detector, tracker = _fakes(_script([QUADRANT_REFS["North"]]))

    report = roi_coverage_report(
        clip_path, clip_config, detector=detector, tracker=tracker
    )
    message = report.mismatch_message()

    assert report.matches is False
    assert report.uncovered == ("East", "South", "West")
    assert message is not None
    assert clip_path in message
    for name in ("East", "South", "West"):
        assert name in message
    assert "North" not in message


def test_uncovered_approaches_are_reported_in_fixed_approach_order(
    clip_path: str, clip_config: Config
) -> None:
    """Ordering is the fixed North, East, South, West, not detection order."""
    detector, tracker = _fakes(_script([QUADRANT_REFS["South"]]))

    uncovered = check_roi_coverage(
        clip_path, clip_config, detector=detector, tracker=tracker
    )

    assert uncovered == ["North", "East", "West"]


def test_tracks_outside_every_roi_are_counted_as_unassigned(
    clip_path: str, gap_config: Config
) -> None:
    """A stale viewpoint shows both symptoms: empty ROIs and unassigned tracks."""
    detector, tracker = _fakes(_script([(32, 24)]))       # the uncovered middle

    report = roi_coverage_report(
        clip_path, gap_config, detector=detector, tracker=tracker
    )

    assert report.uncovered == tuple(APPROACH_NAMES)
    assert report.unassigned == CLIP_FRAME_COUNT
    assert "outside every ROI" in (report.mismatch_message() or "")


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_stride_limits_the_frames_inspected(
    clip_path: str, clip_config: Config
) -> None:
    """Only every stride-th frame is detected, tracked, and assigned (Req 16.5)."""
    detector, tracker = _fakes(_script(list(QUADRANT_REFS.values())))

    report = roi_coverage_report(
        clip_path, clip_config, detector=detector, tracker=tracker, stride=4
    )

    assert report.frames_sampled == 2                    # indices 0 and 4
    assert detector.calls == 2
    assert report.matches is True


def test_coverage_sample_stride_spreads_samples_over_the_clip() -> None:
    """The stride spans the whole clip rather than only its opening."""
    assert coverage_sample_stride(600, 60) == 10
    assert coverage_sample_stride(60, 60) == 1
    assert coverage_sample_stride(5, 60) == 1
    assert coverage_sample_stride(0, 60) == 1


def test_invalid_sampling_arguments_are_rejected(
    clip_path: str, clip_config: Config
) -> None:
    with pytest.raises(ValueError):
        coverage_sample_stride(100, 0)
    with pytest.raises(ValueError):
        roi_coverage_report(clip_path, clip_config, tracker=FakeTracker(), stride=0)

"""Tests for the full SignalOverlay draw stack and the annotated video writer.

Requirements 12.1 to 12.7: the region layer, the track layer, the per-approach
panel, the traffic-light glyphs, the phase banner, the header, and one written
output frame per drawn frame.

Text-bearing layers are asserted through the string helpers
(:func:`~src.overlay.signal_banner_text`, :func:`~src.overlay.signal_header_text`)
rather than by reading pixels back: the string is the contract, and a pixel
assertion on rendered glyphs would fail on a font change without anything being
wrong. What *is* asserted on pixels is what only pixels can show — that a layer
drew at all, that the lit lamp carries its state's colour, and that the input frame
is never mutated.

Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.config import APPROACH_NAMES, Config, load_config
from src.errors import VideoError
from src.lane_analysis import AssignedTrack
from src.overlay import (
    LAMP_ORDER,
    SIGNAL_LAMP_COLORS,
    AnnotatedVideoWriter,
    SignalOverlay,
    default_control_output_path,
    draw_queue_markers_on,
    draw_signal_lights_on,
    lamp_color,
    signal_banner_text,
    signal_header_text,
)
from src.signal_controller import (
    AdaptiveController,
    PhaseInfo,
    PhaseSequencer,
    SignalState,
)
from src.tracking import Track, new_trajectory
from src.traffic_metrics import ApproachMetrics
from tests.fixtures import synthetic_frame

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

FRAME_RATE = 4.0


@pytest.fixture
def config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _phase_info(
    *,
    approach: str | None = "North",
    state: SignalState = SignalState.GREEN,
    green_time: float = 45.0,
    remaining_seconds: float = 12.5,
    selection_score: float = 0.72,
    selection_changed: bool = False,
    starvation_override: bool = False,
    cycle_index: int = 0,
) -> PhaseInfo:
    return PhaseInfo(
        frame_index=0,
        approach=approach,
        state=state,
        phase_index=0,
        cycle_index=cycle_index,
        green_time=green_time,
        phase_seconds=green_time,
        remaining_seconds=remaining_seconds,
        selection_score=selection_score,
        starvation_override=starvation_override,
        selection_changed=selection_changed,
        phase_started=True,
    )


def _metrics() -> dict[str, ApproachMetrics]:
    return {
        name: ApproachMetrics(
            approach=name,
            vehicle_count=index + 1,
            vehicle_density=0.25 * index,
            queue_length=index,
            normalized_queue=0.2 * index,
        )
        for index, name in enumerate(APPROACH_NAMES)
    }


def _states(green: str | None = "North") -> dict[str, SignalState]:
    return {
        name: SignalState.GREEN if name == green else SignalState.RED
        for name in APPROACH_NAMES
    }


def _track(track_id: int = 1) -> Track:
    track = Track(
        track_id=track_id,
        x1=6,
        y1=8,
        x2=20,
        y2=22,
        vehicle_class="car",
        trajectory=new_trajectory(30, [(10, 20), (12, 21), (13, 22)]),
    )
    return track


def _assigned(*, is_queueing: bool = True, approach: str | None = "North") -> AssignedTrack:
    return AssignedTrack(
        track_id=1,
        vehicle_class="car",
        ref_point=(24, 26),
        approach=approach,
        is_queueing=is_queueing,
    )


# ---------------------------------------------------------------------------
# Requirement 12.4, 12.7 — the phase banner text
# ---------------------------------------------------------------------------


def test_banner_names_the_green_approach_with_assigned_and_remaining_seconds() -> None:
    """Requirement 12.4."""
    text = signal_banner_text(_phase_info(green_time=45.0, remaining_seconds=12.5))

    assert "North" in text
    assert "GREEN" in text
    assert "12.5s" in text
    assert "45s" in text


def test_banner_shows_the_selecting_score_when_the_selection_changes() -> None:
    """Requirement 12.7: the Score that produced the new selection is displayed."""
    changed = signal_banner_text(_phase_info(selection_changed=True, selection_score=0.72))
    unchanged = signal_banner_text(_phase_info(selection_changed=False, selection_score=0.72))

    assert "0.72" in changed
    assert "0.72" not in unchanged


def test_banner_calls_out_a_starvation_override() -> None:
    """Requirement 9.6: the Score alone would not explain the choice."""
    text = signal_banner_text(_phase_info(starvation_override=True))

    assert "starvation" in text.lower()


def test_banner_before_the_first_cycle_reports_all_red() -> None:
    """Requirement 10.5: there is no GREEN Approach to name yet."""
    text = signal_banner_text(_phase_info(approach=None, state=SignalState.RED))

    assert "RED" in text
    assert "North" not in text


def test_banner_follows_a_real_sequencer_through_a_cycle(config: Config) -> None:
    """The banner reads its numbers off the sequencer, not off a stand-in."""
    sequencer = PhaseSequencer(AdaptiveController(config), config, FRAME_RATE)
    scores = {name: 0.9 if name == "North" else 0.0 for name in APPROACH_NAMES}

    first = signal_banner_text(sequencer.tick(0, scores))
    later = signal_banner_text(sequencer.tick(4, scores))

    assert first.startswith("North GREEN")
    # One simulated second later the countdown has dropped by one second.
    assert "60s green" in first and "60s green" in later
    assert first != later


# ---------------------------------------------------------------------------
# Requirement 12.5 — the header
# ---------------------------------------------------------------------------


def test_header_carries_the_controller_name_and_simulated_clock() -> None:
    text = signal_header_text("adaptive", 12.3456, _phase_info(cycle_index=2))

    assert "adaptive" in text
    assert "12.35s" in text
    assert "cycle 3" in text


def test_header_omits_the_cycle_before_the_first_one_begins() -> None:
    text = signal_header_text("fixed", 0.0, _phase_info(cycle_index=-1))

    assert "cycle" not in text
    assert "fixed" in text


# ---------------------------------------------------------------------------
# Requirement 12.3 — the traffic-light glyphs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["RED", "YELLOW", "GREEN"])
def test_lit_lamp_takes_its_state_colour_and_unlit_lamps_are_darkened(state: str) -> None:
    lit = lamp_color(state, lit=True)
    unlit = lamp_color(state, lit=False)

    assert lit == SIGNAL_LAMP_COLORS[state]
    assert all(dark <= bright for dark, bright in zip(unlit, lit))
    assert unlit != lit


def test_glyph_row_draws_the_lit_lamp_of_each_approach() -> None:
    """One glyph per Approach, the GREEN one lit in green (Requirement 12.3)."""
    frame = np.zeros((120, 320, 3), dtype=np.uint8)

    draw_signal_lights_on(frame, _states("South"))

    colours = {tuple(int(c) for c in pixel) for pixel in frame.reshape(-1, 3)}
    assert SIGNAL_LAMP_COLORS["GREEN"] in colours, "no lamp was lit green"
    assert SIGNAL_LAMP_COLORS["RED"] in colours, "the three RED approaches drew no lit lamp"
    # Every glyph draws all three lamps, so the unlit colours appear too.
    for lamp in LAMP_ORDER:
        assert lamp_color(lamp, lit=False) in colours


def test_glyph_row_accepts_plain_state_strings() -> None:
    """The Run_Log carries strings, the sequencer carries enum members; both draw."""
    frame = np.zeros((120, 320, 3), dtype=np.uint8)
    from_strings = frame.copy()

    draw_signal_lights_on(frame, _states("North"))
    draw_signal_lights_on(
        from_strings, {name: state.value for name, state in _states("North").items()}
    )

    assert np.array_equal(frame, from_strings)


def test_glyphs_outside_a_small_frame_do_not_raise() -> None:
    """A frame narrower than the glyph column loses the glyphs, it does not fail."""
    frame = synthetic_frame(0)

    assert draw_signal_lights_on(frame, _states()) is frame


# ---------------------------------------------------------------------------
# Queue markers
# ---------------------------------------------------------------------------


def test_queue_markers_ring_only_queueing_assigned_tracks() -> None:
    """A ring marks a counted queueing track; nothing else is drawn.

    The third case — queueing without an Approach — is not tested because
    :class:`~src.lane_analysis.AssignedTrack` refuses to hold it at all, so the
    unassigned branch of the marker loop is reachable only for non-queueing tracks.
    """
    queueing = np.zeros((60, 60, 3), dtype=np.uint8)
    not_queueing = np.zeros((60, 60, 3), dtype=np.uint8)
    unassigned = np.zeros((60, 60, 3), dtype=np.uint8)

    draw_queue_markers_on(queueing, [_assigned(is_queueing=True)])
    draw_queue_markers_on(not_queueing, [_assigned(is_queueing=False)])
    draw_queue_markers_on(
        unassigned, [_assigned(is_queueing=False, approach=None)]
    )

    assert queueing.any()
    assert not not_queueing.any()
    assert not unassigned.any()


# ---------------------------------------------------------------------------
# The full stack (Requirements 12.1 to 12.5, 12.7)
# ---------------------------------------------------------------------------


def test_draw_returns_a_new_frame_and_leaves_the_input_untouched(config: Config) -> None:
    """The caller's frame is also the Tracker's and the Metrics_Engine's input."""
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    original = frame.copy()
    overlay = SignalOverlay(config)

    annotated = overlay.draw(
        frame,
        [_track()],
        [_assigned()],
        _metrics(),
        {name: 0.5 for name in APPROACH_NAMES},
        _states(),
        _phase_info(),
        "adaptive",
        1.25,
    )

    assert annotated is not frame
    assert np.array_equal(frame, original)
    assert annotated.shape == frame.shape
    assert annotated.any(), "the overlay drew nothing"


def test_draw_stacks_every_layer(config: Config) -> None:
    """Each layer adds pixels the layers before it did not (Requirements 12.1-12.5)."""
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    overlay = SignalOverlay(config)

    full = overlay.draw(
        frame,
        [_track()],
        [_assigned()],
        _metrics(),
        {name: 0.5 for name in APPROACH_NAMES},
        _states(),
        _phase_info(),
        "adaptive",
        1.25,
    )
    without_tracks = overlay.draw(
        frame,
        [],
        [],
        _metrics(),
        {name: 0.5 for name in APPROACH_NAMES},
        _states(),
        _phase_info(),
        "adaptive",
        1.25,
    )

    assert not np.array_equal(full, without_tracks)
    # The lit GREEN lamp of the served Approach is present in both, since the glyph
    # layer does not depend on the tracks.
    for image in (full, without_tracks):
        colours = {tuple(int(c) for c in pixel) for pixel in image.reshape(-1, 3)}
        assert SIGNAL_LAMP_COLORS["GREEN"] in colours


def test_draw_reflects_the_signal_state_it_is_given(config: Config) -> None:
    """A different served Approach draws a different frame (Requirement 12.3)."""
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    overlay = SignalOverlay(config)
    args = (
        [_track()],
        [_assigned()],
        _metrics(),
        {name: 0.5 for name in APPROACH_NAMES},
    )

    north = overlay.draw(frame, *args, _states("North"), _phase_info(), "adaptive", 1.0)
    south = overlay.draw(
        frame, *args, _states("South"), _phase_info(approach="South"), "adaptive", 1.0
    )

    assert not np.array_equal(north, south)


# ---------------------------------------------------------------------------
# Requirement 12.6 — the annotated video writer
# ---------------------------------------------------------------------------


def _decoded_frame_count(path: str | Path) -> int:
    capture = cv2.VideoCapture(str(path))
    assert capture.isOpened(), f"output video could not be reopened: {path}"
    try:
        count = 0
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                return count
            count += 1
    finally:
        capture.release()


def test_writer_writes_one_frame_per_call(tmp_path: Path) -> None:
    """Requirement 12.6: every drawn frame reaches the file."""
    out_path = tmp_path / "control.mp4"
    frames = [np.full((48, 64, 3), level, dtype=np.uint8) for level in (10, 60, 110, 160)]

    with AnnotatedVideoWriter(str(out_path), 12.0) as writer:
        for frame in frames:
            writer.write(frame)
        assert writer.frames_written == len(frames)
        assert writer.size == (64, 48)
        assert writer.is_open

    assert _decoded_frame_count(out_path) == len(frames)


def test_writer_opens_lazily_from_the_first_frame(tmp_path: Path) -> None:
    """No file is created for a run that draws nothing."""
    out_path = tmp_path / "unused.mp4"
    writer = AnnotatedVideoWriter(str(out_path), 12.0)

    writer.close()

    assert writer.frames_written == 0
    assert not out_path.exists()


def test_writer_resizes_a_mismatched_frame_instead_of_dropping_it(tmp_path: Path) -> None:
    """``cv2.VideoWriter`` discards off-size frames silently; resizing keeps counts equal."""
    out_path = tmp_path / "resized.mp4"

    with AnnotatedVideoWriter(str(out_path), 10.0) as writer:
        writer.write(np.full((48, 64, 3), 30, dtype=np.uint8))
        writer.write(np.full((24, 32, 3), 90, dtype=np.uint8))

    assert _decoded_frame_count(out_path) == 2


def test_writing_after_close_is_an_error(tmp_path: Path) -> None:
    writer = AnnotatedVideoWriter(str(tmp_path / "closed.mp4"), 10.0)
    writer.write(np.full((48, 64, 3), 30, dtype=np.uint8))
    writer.close()
    writer.close()          # idempotent, so a finally block may always call it

    with pytest.raises(VideoError, match="closed"):
        writer.write(np.full((48, 64, 3), 30, dtype=np.uint8))


def test_default_control_output_path_is_traceable_to_its_input() -> None:
    path = default_control_output_path("videos/junction_a.mp4")

    assert path.replace("\\", "/").startswith("results/videos/")
    assert "junction_a" in path
    assert path.endswith("__control.mp4")

"""Property-based tests for the Config_Loader (Property 20).

The strategies below build only configurations that ``load_config`` accepts, so
the round-trip property is exercised over the accepted domain rather than over
arbitrary structs. ``test_generated_configuration_is_accepted_by_load_config``
is what keeps that claim honest.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import (
    APPROACH_NAMES,
    VEHICLE_CLASSES,
    ApproachConfig,
    Config,
    GreenTimeBand,
    from_json_obj,
    load_config,
    to_json_obj,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Printable ASCII keeps generated strings safe to write through JSON text.
_PRINTABLE = st.characters(min_codepoint=32, max_codepoint=126)

#: ``alpha`` is generated over the whole legal range, with the 0 and 1 boundary
#: values of Requirement 6.7 given explicit weight.
_alpha = st.one_of(
    st.just(0.0),
    st.just(1.0),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)

#: ``starvation_limit`` is an int >= 1, with the boundary value 1 weighted.
_starvation_limit = st.one_of(st.just(1), st.integers(min_value=1, max_value=20))

_positive = st.floats(
    min_value=0.001, max_value=1000.0, allow_nan=False, allow_infinity=False
)

_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


def _rectangle(x0: int, y0: int, x1: int, y1: int, midpoints: bool) -> tuple[tuple[int, int], ...]:
    """Return an axis-aligned rectangle, optionally with edge midpoints added.

    Adding midpoints varies the vertex count without changing the covered area,
    so nested-rectangle containment still holds.
    """
    if not midpoints:
        return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    mx, my = (x0 + x1) // 2, (y0 + y1) // 2
    return (
        (x0, y0), (mx, y0), (x1, y0), (x1, my),
        (x1, y1), (mx, y1), (x0, y1), (x0, my),
    )


@st.composite
def _approach_configs(draw: Any, name: str, width: int, height: int) -> ApproachConfig:
    """Build one valid Approach: a rectangular ROI with a nested queue region."""
    x0 = draw(st.integers(min_value=0, max_value=width - 5))
    x1 = draw(st.integers(min_value=x0 + 4, max_value=width - 1))
    y0 = draw(st.integers(min_value=0, max_value=height - 5))
    y1 = draw(st.integers(min_value=y0 + 4, max_value=height - 1))
    roi = _rectangle(x0, y0, x1, y1, draw(st.booleans()))

    qx0 = draw(st.integers(min_value=x0 + 1, max_value=x1 - 2))
    qx1 = draw(st.integers(min_value=qx0 + 1, max_value=x1 - 1))
    qy0 = draw(st.integers(min_value=y0 + 1, max_value=y1 - 2))
    qy1 = draw(st.integers(min_value=qy0 + 1, max_value=y1 - 1))
    if draw(st.booleans()):
        queue: tuple[tuple[int, int], ...] = _rectangle(qx0, qy0, qx1, qy1, False)
    else:
        queue = ((qx0, qy1), (qx1, qy1), ((qx0 + qx1) // 2, qy0))

    return ApproachConfig(
        name=name,
        roi_polygon=roi,
        queue_region=queue,
        saturation_count=draw(_positive),
        queue_capacity=draw(_positive),
    )


@st.composite
def _green_time_bands(
    draw: Any, min_green_time: float, max_green_time: float
) -> tuple[GreenTimeBand, ...]:
    """Build non-empty bands starting at score 0, strictly increasing in score."""
    later_scores = sorted(
        draw(
            st.lists(
                st.floats(
                    min_value=0.0,
                    max_value=1.0,
                    exclude_min=True,
                    allow_nan=False,
                    allow_infinity=False,
                ),
                min_size=0,
                max_size=3,
                unique=True,
            )
        )
    )
    scores = [0.0, *later_scores]
    green_times = sorted(
        draw(
            st.lists(
                st.floats(
                    min_value=min_green_time,
                    max_value=max_green_time,
                    allow_nan=False,
                    allow_infinity=False,
                ),
                min_size=len(scores),
                max_size=len(scores),
            )
        )
    )
    return tuple(
        GreenTimeBand(min_score=score, green_time=green_time)
        for score, green_time in zip(scores, green_times)
    )


@st.composite
def _configs(draw: Any) -> Config:
    """Build a configuration that ``load_config`` accepts."""
    width = draw(st.integers(min_value=16, max_value=1920))
    height = draw(st.integers(min_value=16, max_value=1080))

    min_green_time = draw(
        st.floats(min_value=1.0, max_value=60.0, allow_nan=False, allow_infinity=False)
    )
    max_green_time = min_green_time + draw(
        st.floats(min_value=0.0, max_value=60.0, allow_nan=False, allow_infinity=False)
    )

    return Config(
        alpha=draw(_alpha),
        confidence_threshold=draw(_unit),
        model_path=draw(st.text(alphabet=_PRINTABLE, min_size=1, max_size=40)),
        track_buffer=draw(st.integers(min_value=1, max_value=200)),
        trajectory_length=draw(st.integers(min_value=1, max_value=200)),
        approaches=tuple(
            draw(_approach_configs(name, width, height)) for name in APPROACH_NAMES
        ),
        green_time_bands=draw(_green_time_bands(min_green_time, max_green_time)),
        min_green_time=min_green_time,
        max_green_time=max_green_time,
        yellow_duration=draw(_positive),
        starvation_limit=draw(_starvation_limit),
        default_frame_rate=draw(_positive),
        use_pce_weighting=draw(st.booleans()),
        pce_weights={cls: draw(_positive) for cls in VEHICLE_CLASSES},
        quit_key=draw(st.characters(min_codepoint=33, max_codepoint=126)),
        frame_size=(width, height),
    )


# ---------------------------------------------------------------------------
# Property 20: configurations round-trip
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(_configs())
def test_property_20_round_trip_through_json_object(config: Config) -> None:
    """``from_json_obj(to_json_obj(c)) == c`` for every accepted configuration.

    **Validates: Requirements 14.5**
    """
    assert from_json_obj(to_json_obj(config)) == config


@settings(max_examples=200, deadline=None)
@given(_configs())
def test_property_20_round_trip_through_json_text(config: Config) -> None:
    """The round trip also survives serialization through actual JSON text.

    **Validates: Requirements 14.5**
    """
    restored = from_json_obj(json.loads(json.dumps(to_json_obj(config))))
    assert restored == config


@settings(max_examples=100, deadline=None)
@given(_configs())
def test_generated_configuration_is_accepted_by_load_config(config: Config) -> None:
    """Every generated configuration is one the Config_Loader accepts.

    Without this the round-trip property could hold vacuously over structs that
    validation would reject.

    **Validates: Requirements 14.5**
    """
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(to_json_obj(config), file)
        assert load_config(path) == config
    finally:
        os.remove(path)

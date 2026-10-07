"""Unit tests for total Config_Loader validation.

Every test mutates the serialized default configuration into exactly one invalid
state, writes it out, and asserts that ``load_config`` raises ``ConfigError``
whose message names the offending field, approach, or value.

Validates: Requirements 4.1, 4.7, 4.8, 5.10, 6.7, 6.8, 8.8, 8.9, 9.7, 14.1, 14.2
"""

import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from src.config import load_config, to_json_obj
from src.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"

Loader = Callable[[dict[str, Any]], Any]


@pytest.fixture
def default_obj() -> dict[str, Any]:
    """The default configuration as a mutable JSON object."""
    return to_json_obj(load_config(str(DEFAULT_CONFIG_PATH)))


@pytest.fixture
def load_obj(tmp_path: Path) -> Loader:
    """Write a JSON object to a temporary file and load it through the public API."""

    def _load(obj: dict[str, Any]) -> Any:
        path = tmp_path / "candidate.json"
        path.write_text(json.dumps(obj), encoding="utf-8")
        return load_config(str(path))

    return _load


def _approach(obj: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in obj["approaches"]:
        if entry["name"] == name:
            return entry
    raise AssertionError(f"no approach named {name!r} in the default configuration")


# ---------------------------------------------------------------------------
# The shipped default must keep passing validation unchanged
# ---------------------------------------------------------------------------


def test_default_configuration_passes_validation() -> None:
    config = load_config(str(DEFAULT_CONFIG_PATH))
    assert len(config.approaches) == 4


def test_round_tripped_default_configuration_passes_validation(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    assert load_obj(default_obj) == load_config(str(DEFAULT_CONFIG_PATH))


# ---------------------------------------------------------------------------
# Requirement 14.1 / 14.2 — required keys
# ---------------------------------------------------------------------------


def test_absent_required_key_is_named(default_obj: dict[str, Any], load_obj: Loader) -> None:
    del default_obj["starvation_limit"]
    with pytest.raises(ConfigError, match="starvation_limit"):
        load_obj(default_obj)


def test_empty_model_path_is_named(default_obj: dict[str, Any], load_obj: Loader) -> None:
    default_obj["model_path"] = ""
    with pytest.raises(ConfigError, match="model_path"):
        load_obj(default_obj)


def test_non_numeric_scalar_is_named(default_obj: dict[str, Any], load_obj: Loader) -> None:
    default_obj["alpha"] = "half"
    with pytest.raises(ConfigError, match=r"alpha.*'half'"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Requirement 4.1 — the Approach set
# ---------------------------------------------------------------------------


def test_misnamed_approach_names_the_approach_set(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "West")["name"] = "Northwest"
    with pytest.raises(ConfigError, match="approaches.*Northwest"):
        load_obj(default_obj)


def test_duplicate_approach_is_rejected(default_obj: dict[str, Any], load_obj: Loader) -> None:
    _approach(default_obj, "West")["name"] = "North"
    with pytest.raises(ConfigError, match="approaches"):
        load_obj(default_obj)


def test_missing_fourth_approach_is_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["approaches"] = default_obj["approaches"][:3]
    with pytest.raises(ConfigError, match="approaches"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Requirement 4.8 — ROI polygon shape and frame bounds
# ---------------------------------------------------------------------------


def test_roi_polygon_with_two_vertices_names_the_approach(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "East")["roi_polygon"] = [[860, 240], [1000, 240]]
    with pytest.raises(ConfigError, match=r"approach 'East' roi_polygon.*at least 3"):
        load_obj(default_obj)


def test_roi_vertex_beyond_frame_width_names_the_approach(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "East")["roi_polygon"][1] = [1280, 240]
    with pytest.raises(ConfigError, match=r"approach 'East' roi_polygon.*frame bounds"):
        load_obj(default_obj)


def test_negative_roi_vertex_names_the_approach(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "North")["roi_polygon"][0] = [440, -1]
    with pytest.raises(ConfigError, match=r"approach 'North' roi_polygon.*frame bounds"):
        load_obj(default_obj)


def test_non_integer_roi_vertex_names_the_approach(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "South")["roi_polygon"][0] = [440.5, 420]
    with pytest.raises(ConfigError, match=r"approach 'South' roi_polygon.*integer"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Requirement 4.7 — queue region containment
# ---------------------------------------------------------------------------


def test_queue_vertex_outside_parent_roi_names_approach_and_vertex(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "North")["queue_region"][0] = [900, 200]
    with pytest.raises(
        ConfigError,
        match=re.escape("approach 'North' queue_region vertex 0 (900, 200)"),
    ):
        load_obj(default_obj)


def test_queue_vertex_inside_roi_bounding_box_but_outside_polygon_is_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    # An L-shaped ROI whose bounding box contains the notch at (390, 390).
    north = _approach(default_obj, "North")
    north["roi_polygon"] = [[100, 100], [400, 100], [400, 200], [200, 200], [200, 400], [100, 400]]
    north["queue_region"] = [[390, 390], [395, 395], [390, 395]]
    with pytest.raises(
        ConfigError,
        match=re.escape("approach 'North' queue_region vertex 0 (390, 390)"),
    ):
        load_obj(default_obj)


def test_queue_region_with_two_vertices_names_the_approach(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "West")["queue_region"] = [[300, 260], [410, 260]]
    with pytest.raises(ConfigError, match=r"approach 'West' queue_region.*at least 3"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Requirement 5.10 — positive divisors
# ---------------------------------------------------------------------------


def test_zero_saturation_count_names_approach_and_parameter(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "South")["saturation_count"] = 0.0
    with pytest.raises(ConfigError, match=r"approach 'South' saturation_count.*> 0"):
        load_obj(default_obj)


def test_negative_queue_capacity_names_approach_and_parameter(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    _approach(default_obj, "West")["queue_capacity"] = -2.0
    with pytest.raises(ConfigError, match=r"approach 'West' queue_capacity.*-2\.0"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Requirement 6.7 — alpha range
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alpha", [-0.1, 1.5])
def test_alpha_outside_unit_range_names_the_supplied_value(
    default_obj: dict[str, Any], load_obj: Loader, alpha: float
) -> None:
    default_obj["alpha"] = alpha
    with pytest.raises(ConfigError, match=rf"alpha.*{re.escape(repr(alpha))}"):
        load_obj(default_obj)


def test_confidence_threshold_outside_unit_range_names_the_supplied_value(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["confidence_threshold"] = 1.2
    with pytest.raises(ConfigError, match=r"confidence_threshold.*1\.2"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Requirement 9.7 — starvation limit
# ---------------------------------------------------------------------------


def test_starvation_limit_below_one_names_the_supplied_value(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["starvation_limit"] = 0
    with pytest.raises(ConfigError, match=r"starvation_limit.*0"):
        load_obj(default_obj)


def test_non_integer_starvation_limit_names_the_supplied_value(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["starvation_limit"] = 2.5
    with pytest.raises(ConfigError, match=r"starvation_limit.*2\.5"):
        load_obj(default_obj)


def test_boolean_starvation_limit_is_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["starvation_limit"] = True
    with pytest.raises(ConfigError, match=r"starvation_limit.*True"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Requirements 8.8, 8.9 — green time bands
# ---------------------------------------------------------------------------


def test_empty_green_time_bands_are_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["green_time_bands"] = []
    with pytest.raises(ConfigError, match="green_time_bands"):
        load_obj(default_obj)


def test_bands_not_starting_at_zero_are_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["green_time_bands"][0]["min_score"] = 0.1
    with pytest.raises(ConfigError, match=r"green_time_bands.*min_score 0.*0\.1"):
        load_obj(default_obj)


def test_bands_not_strictly_increasing_in_min_score_are_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["green_time_bands"][2]["min_score"] = 0.3
    with pytest.raises(ConfigError, match=r"green_time_bands.*strictly increasing.*min_score"):
        load_obj(default_obj)


def test_bands_decreasing_in_green_time_are_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["green_time_bands"][1]["green_time"] = 30.0
    default_obj["green_time_bands"][2]["green_time"] = 30.0
    default_obj["green_time_bands"][0]["green_time"] = 45.0
    with pytest.raises(ConfigError, match=r"green_time_bands.*non-decreasing.*green_time"):
        load_obj(default_obj)


def test_band_green_time_above_max_is_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["green_time_bands"][2]["green_time"] = 90.0
    with pytest.raises(ConfigError, match=r"green_time_bands\[2\] green_time 90\.0"):
        load_obj(default_obj)


def test_band_green_time_below_min_is_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["green_time_bands"][0]["green_time"] = 10.0
    with pytest.raises(ConfigError, match=r"green_time_bands\[0\] green_time 10\.0"):
        load_obj(default_obj)


def test_band_min_score_outside_unit_range_is_rejected(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["green_time_bands"][2]["min_score"] = 1.4
    with pytest.raises(ConfigError, match=r"green_time_bands\[2\] min_score.*1\.4"):
        load_obj(default_obj)


# ---------------------------------------------------------------------------
# Remaining scalars downstream components depend on
# ---------------------------------------------------------------------------


def test_track_buffer_below_one_is_named(default_obj: dict[str, Any], load_obj: Loader) -> None:
    default_obj["track_buffer"] = 0
    with pytest.raises(ConfigError, match="track_buffer"):
        load_obj(default_obj)


def test_trajectory_length_below_one_is_named(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["trajectory_length"] = 0
    with pytest.raises(ConfigError, match="trajectory_length"):
        load_obj(default_obj)


def test_non_positive_default_frame_rate_is_named(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["default_frame_rate"] = 0.0
    with pytest.raises(ConfigError, match="default_frame_rate"):
        load_obj(default_obj)


def test_non_positive_yellow_duration_is_named(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["yellow_duration"] = -1.0
    with pytest.raises(ConfigError, match="yellow_duration"):
        load_obj(default_obj)


def test_min_green_time_above_max_green_time_is_named(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["min_green_time"] = 70.0
    with pytest.raises(ConfigError, match=r"min_green_time.*max_green_time"):
        load_obj(default_obj)


def test_non_positive_frame_size_is_named(default_obj: dict[str, Any], load_obj: Loader) -> None:
    default_obj["frame_size"] = [1280, 0]
    with pytest.raises(ConfigError, match=r"frame_size.*height"):
        load_obj(default_obj)


def test_missing_pce_weight_names_the_vehicle_class(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    del default_obj["pce_weights"]["bus"]
    with pytest.raises(ConfigError, match=r"pce_weights.*'bus'"):
        load_obj(default_obj)


def test_unknown_pce_weight_names_the_vehicle_class(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["pce_weights"]["tram"] = 4.0
    with pytest.raises(ConfigError, match=r"pce_weights.*'tram'"):
        load_obj(default_obj)


def test_non_positive_pce_weight_names_the_vehicle_class(
    default_obj: dict[str, Any], load_obj: Loader
) -> None:
    default_obj["pce_weights"]["car"] = 0.0
    with pytest.raises(ConfigError, match=r"pce_weights\['car'\]"):
        load_obj(default_obj)


def test_multi_character_quit_key_is_named(default_obj: dict[str, Any], load_obj: Loader) -> None:
    default_obj["quit_key"] = "quit"
    with pytest.raises(ConfigError, match=r"quit_key.*'quit'"):
        load_obj(default_obj)

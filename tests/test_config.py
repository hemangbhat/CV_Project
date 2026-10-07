"""Unit tests for the Config_Loader dataclasses and JSON (de)serialization."""

import json
from pathlib import Path

import pytest

from src.config import (
    ApproachConfig,
    Config,
    GreenTimeBand,
    from_json_obj,
    load_config,
    to_json_obj,
)
from src.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"


@pytest.fixture
def default_config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def test_load_default_config_reads_documented_scalars(default_config: Config) -> None:
    assert default_config.alpha == 0.5
    assert default_config.min_green_time == 30
    assert default_config.max_green_time == 60
    assert default_config.yellow_duration == 3
    assert default_config.starvation_limit == 3
    assert default_config.frame_size == (1280, 720)


def test_load_default_config_reads_green_time_bands(default_config: Config) -> None:
    assert default_config.green_time_bands == (
        GreenTimeBand(min_score=0.0, green_time=30.0),
        GreenTimeBand(min_score=0.3, green_time=45.0),
        GreenTimeBand(min_score=0.6, green_time=60.0),
    )


def test_load_default_config_reads_four_named_approaches(default_config: Config) -> None:
    assert len(default_config.approaches) == 4
    assert [a.name for a in default_config.approaches] == ["North", "East", "South", "West"]


def test_default_approach_geometry_is_usable(default_config: Config) -> None:
    width, height = default_config.frame_size
    for approach in default_config.approaches:
        assert len(approach.roi_polygon) >= 3, approach.name
        assert approach.saturation_count > 0
        assert approach.queue_capacity > 0
        for x, y in approach.roi_polygon:
            assert 0 <= x < width and 0 <= y < height, approach.name
        xs = [x for x, _ in approach.roi_polygon]
        ys = [y for _, y in approach.roi_polygon]
        for x, y in approach.queue_region:
            assert min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys), approach.name


def test_polygons_are_tuples_so_the_config_is_deeply_immutable(
    default_config: Config,
) -> None:
    approach = default_config.approaches[0]
    assert isinstance(default_config.approaches, tuple)
    assert isinstance(approach.roi_polygon, tuple)
    assert all(isinstance(vertex, tuple) for vertex in approach.roi_polygon)
    with pytest.raises(Exception):
        approach.saturation_count = 5.0  # type: ignore[misc]


def test_round_trip_of_default_config_is_exact(default_config: Config) -> None:
    assert from_json_obj(to_json_obj(default_config)) == default_config


def test_round_trip_through_json_text_is_exact(default_config: Config) -> None:
    restored = from_json_obj(json.loads(json.dumps(to_json_obj(default_config))))
    assert restored == default_config


def test_round_trip_is_insensitive_to_pce_weight_key_order(default_config: Config) -> None:
    obj = to_json_obj(default_config)
    obj["pce_weights"] = dict(reversed(list(obj["pce_weights"].items())))
    assert from_json_obj(obj) == default_config


def test_missing_key_names_the_absent_key(default_config: Config) -> None:
    obj = to_json_obj(default_config)
    del obj["alpha"]
    with pytest.raises(ConfigError, match="alpha"):
        from_json_obj(obj)


def test_missing_approach_key_names_the_approach(default_config: Config) -> None:
    obj = to_json_obj(default_config)
    del obj["approaches"][2]["queue_capacity"]
    with pytest.raises(ConfigError, match="queue_capacity"):
        from_json_obj(obj)


def test_missing_file_raises_config_error_naming_the_path(tmp_path: Path) -> None:
    absent = tmp_path / "nope.json"
    with pytest.raises(ConfigError, match="nope.json"):
        load_config(str(absent))


def test_malformed_json_raises_config_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="valid JSON"):
        load_config(str(broken))


def test_approach_lookup_by_name(default_config: Config) -> None:
    assert default_config.approach("West").name == "West"
    with pytest.raises(ConfigError, match="Northeast"):
        default_config.approach("Northeast")


def test_dataclass_equality_ignores_construction_route(default_config: Config) -> None:
    hand_built = ApproachConfig(
        name="North",
        roi_polygon=((440, 0), (840, 0), (840, 300), (440, 300)),
        queue_region=((460, 200), (820, 200), (820, 290), (460, 290)),
        saturation_count=12.0,
        queue_capacity=8.0,
    )
    assert default_config.approach("North") == hand_built

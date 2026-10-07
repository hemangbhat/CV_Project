"""Property-based tests for the Video_Ingestor (Property 18).

Clip lengths are generated rather than fixed, so the frame-index invariant is
checked at the awkward lengths too: a one-frame clip, where the frame buffered
during ``__init__`` is the *only* frame, and lengths where the encoder may flush
in more than one chunk.

Each example writes and decodes a real clip, so the example count is kept modest
and frames are tiny; the invariant does not depend on resolution.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.config import Config, load_config
from src.video_io import VideoIngestor
from tests.fixtures import write_synthetic_clip

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"


def _config() -> Config:
    return load_config(str(DEFAULT_CONFIG_PATH))


def _read_indices(clip: Path, config: Config) -> list[int]:
    ingestor = VideoIngestor(str(clip), config)
    try:
        return [index for index, _frame in ingestor.frames()]
    finally:
        ingestor.close()


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    frame_count=st.integers(min_value=1, max_value=15),
    frame_rate=st.sampled_from([10.0, 20.0, 30.0]),
)
def test_property_18_frame_indices_are_consecutive_from_zero(
    frame_count: int, frame_rate: float
) -> None:
    """Indices run 0, 1, 2, ... with none skipped and none repeated.

    **Validates: Requirements 1.2**
    """
    config = _config()
    with tempfile.TemporaryDirectory() as directory:
        clip = write_synthetic_clip(
            Path(directory) / "clip.mp4", frame_count=frame_count, frame_rate=frame_rate
        )
        indices = _read_indices(clip, config)

    assert indices, "a readable clip must yield at least one frame"
    assert indices[0] == 0
    assert len(set(indices)) == len(indices), f"repeated index in {indices}"
    assert indices == list(range(len(indices))), f"index skipped or out of order in {indices}"
    # The buffered first frame is handed out rather than dropped or re-read, so
    # every written frame arrives exactly once.
    assert len(indices) == frame_count

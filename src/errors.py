"""Error hierarchy for the adaptive traffic signal system.

Every error raised deliberately by this project derives from
:class:`TrafficSignalError`, so ``src/main.py`` needs exactly one
``except TrafficSignalError`` clause to report a failure and exit with a
non-zero status code.
"""


class TrafficSignalError(Exception):
    """Base class for every error raised by this project."""


class ConfigError(TrafficSignalError):
    """Configuration is absent, malformed, or fails validation.

    Raised by the Config_Loader. The message names the offending field,
    approach, or value so the operator can fix the configuration file.
    """


class VideoError(TrafficSignalError):
    """An input video cannot be opened, decoded, or read.

    Raised by the Video_Ingestor. The message names the supplied path.
    """


class ModelError(TrafficSignalError):
    """Detector weights cannot be loaded.

    Raised by the Detector. The message names the configured model path.
    """


class EvaluationError(TrafficSignalError):
    """The evaluation input set or a Run_Log is unusable.

    Raised by the Evaluation_Harness, for example when the video set holds
    fewer than 2 or more than 5 videos.
    """


__all__ = [
    "TrafficSignalError",
    "ConfigError",
    "VideoError",
    "ModelError",
    "EvaluationError",
]

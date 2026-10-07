"""Closed-loop evaluation of the signal controllers in SUMO.

Recorded video cannot rank controllers: the recorded vehicles move when the *real*
signal lets them, so any signal-gated metric measures agreement with that signal
(AUDIT_REPORT.md, finding W1). This package closes the loop. A four-arm junction is
simulated in SUMO, the vehicles respond to the simulated signal, and the controller
under test is the project's own code, imported unchanged from ``src``:
``AdaptiveController``, ``PhaseSequencer``, ``QueuePredictor``,
``compute_config_scores`` and ``queue_tail_reach``.

Only the sensor differs. On video the per-approach measurements come from YOLO +
ByteTrack; here they come from the simulated vehicles' positions and speeds, computed
with the same definitions (``sim/sensor.py``). An optional noise model degrades that
sensor the way the vision pipeline is degraded (missed far detections, position
jitter), so the effect of measurement quality on control can be tested as well.
"""

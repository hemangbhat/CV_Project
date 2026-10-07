# Archived documents — superseded, kept for the record

These are the project documents as they stood **before the October 2026 audit**
(`AUDIT_REPORT.md`). They are kept because they record how the project evolved,
including honest negative results (E1–E8), but **their headline results are superseded**:

* The busy-clip "S3 → S4 +21% throughput" result is reproduced exactly by a weight-matched
  null control with the spillback-risk term set to zero (audit W2), and open-loop
  "vehicles served" measures overlap with the *real* recorded signal rather than control
  quality (audit W1).
* "S3 is inert because the count queue saturates" does not describe the real footage: the
  count queue is saturated in under 1% of frames (audit W3).
* The original ROIs mix legs and travel directions and two calibrated axes are reversed,
  so legacy queue-reach values are not queue extents (audit W7).

The current, correct account is in `README.md` and `report/FINAL_REPORT.md`.
`report_legacy/` holds the figures and tables those documents cite; the run logs they
reference are in `results/run_logs_legacy/`.

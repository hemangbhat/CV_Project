# Documentation index

Everything needed to understand, reproduce and present the project. Read in this order.

| # | Document | Read it to |
|---|---|---|
| 1 | [`EXPLAIN_TO_PROFESSOR.md`](EXPLAIN_TO_PROFESSOR.md) | Know the story, what is yours vs borrowed, the key numbers, the 20-minute talk and hard questions |
| 1b | [`WORKED_EXAMPLE.md`](WORKED_EXAMPLE.md) | Follow one real camera frame through every formula: box → position p → stopped → X → S → score, with every number computed by hand (`python audit/explain_frame.py`) |
| 2 | [`../report/RESEARCH_PRESENTATION.md`](../report/RESEARCH_PRESENTATION.md) | See the whole project in the department's presentation format, with every result table |
| 3 | [`../PROJECT_EXPLAINED.md`](../PROJECT_EXPLAINED.md) | Look up any term or abbreviation (SUMO, PCE, FASC, CI, β, ...) |
| 4 | [`TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md) | Understand each stage: formulas, code locations, worked examples (§20 = study 2) |
| 5 | [`architecture.md`](architecture.md) | See how the modules connect, for video and both simulation studies |
| 6 | [`STUDY_GUIDE.md`](STUDY_GUIDE.md) | Learn the project in a week, with exercises to run |
| 7 | [`VIVA_QA.md`](VIVA_QA.md) | Rehearse answers to expected questions (section E = study 2) |
| 8 | [`../report/FINAL_REPORT.md`](../report/FINAL_REPORT.md) | Read the full research report, including the audit and both studies |
| 9 | [`../AUDIT_REPORT.md`](../AUDIT_REPORT.md) | See what was wrong in the earlier results and how it was found |
| 10 | [`../sim/PROTOCOL.md`](../sim/PROTOCOL.md), [`../sim/PROTOCOL_STUDY2.md`](../sim/PROTOCOL_STUDY2.md) | See the experiment plans, fixed before the test runs |
| 11 | [`papers/README.md`](papers/README.md) | Check the verified facts taken from Raza, Li, Wei and Mohajerpoor |
| 12 | [`research/`](research/) | Literature review, limitation analysis of each paper (§7 = Mohajerpoor), further T-ITS papers |
| — | [`archive/`](archive/) | Pre-audit documents, kept for the record; their results are superseded |

`../COMPLETE_PROJECT_DOCUMENT.md` combines these documents (except the archive) into one file with a short
"In simple terms" note after each part (`python scripts/build_complete_doc.py`).

## The project in three lines

1. **Measurement (C1):** the queue end is measured from YOLOv8 + ByteTrack tracks along a
   drawn queue axis, with a size-scaled stopped test; it works wherever the detector sees
   the cars.
2. **Study 1 (C2):** adding the queue measure to Raza's ranking score changes nothing
   (±0.5 s), because the longest queue is also the one with most cars; the green-time rule
   is what matters.
3. **Study 2 (C3, final enhancement):** used as a storage limit in the green timing
   (after Mohajerpoor 2023, but measured instead of modelled), it cuts local spillback by
   20–26% against actuated control near capacity at the same delay, beats a simple count
   on a short road, and fails beyond capacity.

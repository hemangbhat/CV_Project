# Paper

The same report in two IEEEtran layouts:

| Layout | Source | Compiled PDF |
|---|---|---|
| Conference | `main.tex` | [Conference report](Vision_Measured_Queue_Tails_Paper.pdf) |
| Journal | `main_journal.tex` | [Journal report](Vision_Measured_Queue_Tails_Paper_journal.pdf) |

Authors: **Hemang Bhat (2024UCA1829)** and **Jatin (2024UCA1865)**, NSUT.
No email address has been guessed.

## Overleaf

Upload [overleaf_upload.zip](overleaf_upload.zip) as a new project. Use pdfLaTeX and
select `main.tex` or `main_journal.tex` as the main document. Both source files contain
the authors and roll numbers. Add an institute email only if your course requires it.

## Reproduce

From the repository root:

```bash
python audit/verify_paper_results.py
cd paper
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main_journal.tex
pdflatex -interaction=nonstopmode -halt-on-error main_journal.tex
```

The [submission workflow](../../../actions/workflows/submission-check.yml) runs the
tests, verifies tables against the raw logs, replays representative SUMO runs, compiles
both layouts and rebuilds the two PDFs and the ZIP together.

Table I is a **YOLOv8n** measurement audit from `report/measurement_compare.json`.
The final YOLOv8m measurements are described separately. Table II uses the saturating
density normaliser. Tables III-V come from the raw logs in `results/sim/study2/`.
The independent checker verifies all 176 Study 2 values, 25 Study 1 means and the
three Table I stop-event values in both sources.

[Submission audit](../docs/SUBMISSION_AUDIT.md) records the claim and method corrections.

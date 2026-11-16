# Paper

`main.tex` is the project report in IEEE conference format (IEEEtran).
`Vision_Measured_Queue_Tails_Paper.pdf` is the compiled version.

## Edit on Overleaf
1. Overleaf → New Project → Upload Project → choose `overleaf_upload.zip`.
2. Compiler: pdfLaTeX. Main document: `main.tex`.
3. Fill in the author block (roll number, e-mail) near the top of `main.tex`.

## Compile locally
```
pdflatex main.tex && pdflatex main.tex
```

Every number in the paper comes from `results/` in this repository:
Table I from `report/measurement_compare.json`, Table II from `results/sim/summary.json`,
Tables III-V and Fig. 3 from `results/sim/study2/` (`python -m sim.study2_figures`).

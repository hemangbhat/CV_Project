# Technical documentation

| Document | Contents |
|---|---|
| [Technical guide](TECHNICAL_GUIDE.md) | Formulas, implementation details, code locations and design choices |
| [Worked example](WORKED_EXAMPLE.md) | A real frame traced through the measurement pipeline; reproduce with `python audit/explain_frame.py --frame 2036 --approach West` |
| [Architecture](architecture.md) | Module map and data flow for video processing and simulation |
| [Submission audit](SUBMISSION_AUDIT.md) | Numerical verification, corrections and limitations |
| [Final report](../report/FINAL_REPORT.md) | Detailed account of the measurement pipeline and both experimental studies |
| [Research presentation](../report/RESEARCH_PRESENTATION.md) | Project methods and result tables in presentation order |
| [Repository audit](../AUDIT_REPORT.md) | Earlier evaluation problems and how they were diagnosed |
| [Study 1 protocol](../sim/PROTOCOL.md) | Frozen experiment design for score-based control |
| [Study 2 protocol](../sim/PROTOCOL_STUDY2.md) | Frozen experiment design for storage-aware timing |
| [Reference papers](papers/README.md) | Source papers and the facts used from each |
| [Literature analysis](research/) | Research comparison and limitation analysis |
| [Archived records](archive/) | Superseded analyses and historical figures; use the final reports for current conclusions |

## Submission reports

- [Conference PDF](../paper/Vision_Measured_Queue_Tails_Paper.pdf)
- [Journal PDF](../paper/Vision_Measured_Queue_Tails_Paper_journal.pdf)
- [Overleaf package](../paper/overleaf_upload.zip)

The reports have identical content in two layouts. Run `python audit/verify_paper_results.py`
from the repository root to check the numerical tables against the raw logs. The latest
completed verification is recorded in [submission_validation.txt](../audit/submission_validation.txt).

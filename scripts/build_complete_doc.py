"""Build COMPLETE_PROJECT_DOCUMENT.md: every project document in one file, with a short
"In simple terms" explanation after each part.

The content is pulled from the source documents at build time, so the combined file never
drifts from them. Re-run after editing any source document:

    python scripts/build_complete_doc.py
"""
from __future__ import annotations

import re
from pathlib import Path

OUT = Path("COMPLETE_PROJECT_DOCUMENT.md")

# (title of the part in the combined document, source file, what to keep)
PARTS = [
    ("Overview", "README.md", "from:## The result in four sentences"),
    ("Final Report", "report/FINAL_REPORT.md", "all"),
    ("System Architecture", "docs/architecture.md", "all"),
    ("Technical Guide", "docs/TECHNICAL_GUIDE.md", "from:## 1. The problem in one paragraph"),
    ("Audit Report", "AUDIT_REPORT.md", "all"),
    ("Closed-Loop Experiment Protocol", "sim/PROTOCOL.md", "all"),
    ("The Papers (verified facts)", "docs/papers/README.md", "all"),
    ("Additional Recent T-ITS Papers", "docs/research/additional_tits_papers.md", "all"),
    ("Glossary of Concepts and Terms", "PROJECT_EXPLAINED.md", "from:## Part 2 — Traffic-engineering terms"),
    ("Viva Questions and Answers", "docs/VIVA_QA.md", "from:## A. Problem and research chain"),
    ("Study Guide and Presentation Plan", "docs/STUDY_GUIDE.md", "from:## Stage 1 — The story (day 1)"),
]

# "In simple terms" notes, keyed by (source file, heading text as written in the source).
SIMPLE: dict[tuple[str, str], str] = {
    # ---------------- README ----------------
    ("README.md", "The result in four sentences"):
        "The camera measurement now works. The old \"+21%\" result was a measurement mistake. "
        "In a fair test, adding queue reach to the decision did not reduce delay. What reduced "
        "delay was ending each green once its queue has cleared.",
    ("README.md", "Read in this order"):
        "A reading list. This combined document already contains all of these files.",
    ("README.md", "Repository map"):
        "Where things live: `src/` is the system, `sim/` is the simulation test, `config/` holds "
        "settings, `results/` holds outputs, `report/` holds the report and figures, `tests/` "
        "holds the automatic checks.",
    ("README.md", "Setup"):
        "Install the Python libraries, download the videos, and run the tests to confirm everything works.",
    ("README.md", "Run it"):
        "The commands to detect and track vehicles once, make the demo video, check the "
        "measurement, and rerun the simulation experiment.",
    ("README.md", "Data"):
        "Three real traffic-camera videos from the City of Bellevue (USA), from one junction, "
        "converted to exactly 30 frames per second.",
    ("README.md", "What this project does not claim"):
        "Things you must not say: that you reproduced the papers exactly, that the video proves "
        "real-world improvement, that you detect downstream spillback, or that queue reach improves control.",
    # ---------------- FINAL REPORT ----------------
    ("report/FINAL_REPORT.md", "Abstract"):
        "The whole project in one paragraph: what was built, what was fixed, how it was tested, "
        "what was found and why.",
    ("report/FINAL_REPORT.md", "1. Problem"):
        "A traffic light must decide which road goes next and for how long. The question here "
        "is what a camera should measure to make that decision well.",
    ("report/FINAL_REPORT.md", "2. Base paper: Raza et al. (2025)"):
        "Raza's system counts vehicles with a camera, weights big vehicles more, gives green to "
        "the busiest road, and sets the green length from how busy it is. A counter makes sure "
        "no road is skipped forever. My S1 copies this logic but is not an exact copy. Its "
        "weakness, which I identified myself, is that it only looks at the current count.",
    ("report/FINAL_REPORT.md", "3. Newer research: the two IEEE T-ITS papers"):
        "Li looks at how the queue builds and whether a green is long enough to clear it. Wei "
        "predicts how queues will grow to stop them blocking other roads. Both need equipment "
        "and solvers a single camera project does not have, so I used their ideas, not their machinery.",
    ("report/FINAL_REPORT.md", "4. Our enhancement"):
        "My idea: measure how far back the line of stopped cars reaches, and predict it a few "
        "seconds ahead.",
    ("report/FINAL_REPORT.md", "4.1 The blind spot (proved from the implementation)"):
        "Counting cars in a small box at the stop line stops working once the box is full. Extra "
        "cars wait behind it, so the count stays at \"full\", and a prediction based on that "
        "count also stays stuck.",
    ("report/FINAL_REPORT.md", "4.2 Spatial queue reach X and spillback risk S"):
        "X is how far back along the road the line of stopped cars reaches (0 = at the stop line, "
        "1 = the end of what the camera sees). S is X projected 5 seconds ahead. If the line is "
        "growing, S is bigger than X.",
    ("report/FINAL_REPORT.md", "4.3 The score"):
        "Each road gets a priority number between 0 and 1 made from several measurements. The "
        "highest number usually gets green.",
    ("report/FINAL_REPORT.md", "4.4 What is genuinely ours, and what is not"):
        "Detection, tracking, density and the traffic-control ideas come from others and are "
        "cited. The queue-reach measurement, its prediction, the analysis and the fair test are mine.",
    ("report/FINAL_REPORT.md", "5. System"):
        "The pipeline: video → find vehicles → follow each vehicle → decide which road it is on → "
        "measure → score → choose the green → draw the overlay and save logs.",
    ("report/FINAL_REPORT.md", "6. Experiments and results"):
        "Three kinds of evidence: checking the old results, checking the measurement on real "
        "video, and a fair simulation test.",
    ("report/FINAL_REPORT.md", "6.1 Audit of the earlier results (what did *not* hold up)"):
        "The old \"+21%\" was wrong for two reasons. The recorded cars obey the real light that "
        "was filmed, not my simulated one, so \"cars served\" measures luck. And a hidden weight "
        "change, not my new measure, caused the difference. Several other measurement problems "
        "were also found.",
    ("report/FINAL_REPORT.md", "6.2 Measurement on real footage (after the fixes)"):
        "After the fixes the numbers became sensible (far fewer false \"stops\", stable queue "
        "reach), and the measured end of the queue matches the real queue in the pictures.",
    ("report/FINAL_REPORT.md", "6.3 Closed-loop evaluation (SUMO)"):
        "In the simulator the cars obey my light, so it is a fair test. Ending a green once the "
        "queue has cleared cut delay a lot. Adding queue reach or its prediction made no real "
        "difference, and that is explained: it almost never changes which road is chosen.",
    ("report/FINAL_REPORT.md", "6.4 Open-loop decision analysis on the footage"):
        "On the real videos, my controller picks the same road as the version without the new "
        "measure at every green. Only green lengths differ.",
    ("report/FINAL_REPORT.md", "6.5 Earlier negative results (kept; detail in `docs/archive/`)"):
        "Earlier experiments that did not help are kept on purpose. Honest research reports what failed.",
    ("report/FINAL_REPORT.md", "7. Discussion"):
        "Why it turned out this way: the camera's view also fills up, so queue reach gets stuck "
        "too; and the longest queue is usually also the busiest road. Timing is where prediction "
        "helps, which matches newer research.",
    ("report/FINAL_REPORT.md", "8. Limitations"):
        "What the project cannot show: real-street results, far-away cars (often missed), roads "
        "beyond the junction, and an exact copy of Raza.",
    ("report/FINAL_REPORT.md", "9. Future work"):
        "Next steps: see the downstream road, use queue reach to set green length, detect far "
        "cars better, and test more junctions.",
    ("report/FINAL_REPORT.md", "10. Reproducibility"):
        "Every number can be regenerated from files and commands in the repository.",
    ("report/FINAL_REPORT.md", "References"):
        "The papers and tools the project relies on.",
    # ---------------- ARCHITECTURE ----------------
    ("docs/architecture.md", "The two feedback loops"):
        "On recorded video, cars cannot react to my light (open loop). In the simulator they do "
        "(closed loop). Only the second can show which controller is better.",
    ("docs/architecture.md", "Order of operations per frame (identical in both front ends)"):
        "Every frame: update the light, measure the roads, predict ahead, compute scores. The "
        "next decision uses these scores.",
    ("docs/architecture.md", "Where each module lives"):
        "Which file does which job.",
    # ---------------- TECHNICAL GUIDE ----------------
    ("docs/TECHNICAL_GUIDE.md", "1. The problem in one paragraph"):
        "Can measuring how far a queue reaches, and where it is heading, make better green "
        "decisions than just counting cars?",
    ("docs/TECHNICAL_GUIDE.md", "2. Video input and the simulated clock"):
        "Time is counted in video frames, so the video must have a steady 30 frames per second, "
        "or every time measurement would be wrong.",
    ("docs/TECHNICAL_GUIDE.md", "3. Detection: YOLOv8"):
        "YOLO draws a box around every vehicle in each frame. The bigger model (v8m) finds many "
        "more distant cars, which matters because the end of a queue is far away.",
    ("docs/TECHNICAL_GUIDE.md", "4. Tracking: ByteTrack and the track cache"):
        "ByteTrack gives each vehicle an ID that stays the same across frames, which is needed to "
        "know if a vehicle has stopped. The tracking result is saved once and replayed, so every "
        "controller sees exactly the same vehicles.",
    ("docs/TECHNICAL_GUIDE.md", "5. Geometry: ROI, queue strip, queue axis"):
        "On the image I drew, for each road: an area covering its incoming lanes, a strip at the "
        "stop line, and a line from the stop line backwards along the road to measure along. The "
        "first drawing was wrong and was redrawn.",
    ("docs/TECHNICAL_GUIDE.md", "6. Assigning a vehicle to an approach"):
        "Each vehicle is placed by the middle of the bottom of its box (where it touches the "
        "road) and assigned to the area that contains that point.",
    ("docs/TECHNICAL_GUIDE.md", "7. Measurements D, Q, A"):
        "D = how crowded the road is. Q = how full the stop-line strip is. A = vehicles on the "
        "road but not yet at the strip. Q gets stuck at full once the strip is full.",
    ("docs/TECHNICAL_GUIDE.md", "8. Is a vehicle stopped? (the windowed test)"):
        "A vehicle counts as stopped if, over the last second, it moved less than a fifth of its "
        "own size per second. Using one second removes box wobble, and using its own size treats "
        "near and far vehicles fairly.",
    ("docs/TECHNICAL_GUIDE.md", "9. Spatial queue reach X"):
        "Start at the stop line and follow stopped vehicles backwards while they are close behind "
        "each other. Where the chain ends is X. A lonely stopped car far away does not count.",
    ("docs/TECHNICAL_GUIDE.md", "10. Forecasts: count forecast F and spillback risk S"):
        "Both forecasts fit a trend over the last 2.5 seconds and extend it forward. The count "
        "forecast F gets stuck when the strip is full; S, based on X, keeps showing whether the "
        "queue is growing, steady or shrinking.",
    ("docs/TECHNICAL_GUIDE.md", "11. The score"):
        "The priority number is a weighted mix of the measurements. The NULL version exists to "
        "check whether my new measure really caused any change.",
    ("docs/TECHNICAL_GUIDE.md", "12. Choosing the approach: the adaptive controller"):
        "First, any road skipped 3 times in a row goes next. Otherwise, the highest score goes next.",
    ("docs/TECHNICAL_GUIDE.md", "13. Timing the green: bands vs actuated"):
        "Two ways to set green length: from the score (Raza-style), or \"stay green until the "
        "queue has cleared\" (actuated). The score-based rule mixes up two decisions, which is "
        "why the fair test uses actuated timing.",
    ("docs/TECHNICAL_GUIDE.md", "14. The signal state machine"):
        "The light can only go green → yellow → red, and only one road is green at a time. The "
        "code makes any other change impossible.",
    ("docs/TECHNICAL_GUIDE.md", "15. Open-loop metrics, and why they cannot rank controllers"):
        "Recorded cars move when the real filmed light allows. Counting them during my simulated "
        "green measures coincidence, not control quality.",
    ("docs/TECHNICAL_GUIDE.md", "16. The closed-loop simulation"):
        "A simulated junction where cars obey my controller, measured by a pretend camera using "
        "the same rules as the real one. It is tested in many traffic situations, many times each.",
    ("docs/TECHNICAL_GUIDE.md", "17. Statistics: how a difference is judged real"):
        "Every controller faces the same random traffic, and we look at the difference per run. "
        "A difference is real only if its 95% range does not include zero.",
    ("docs/TECHNICAL_GUIDE.md", "18. Testing"):
        "793 automatic tests check that every part behaves as intended.",
    ("docs/TECHNICAL_GUIDE.md", "19. Reproducing every number"):
        "Copy-paste commands to regenerate all results.",
    # ---------------- AUDIT ----------------
    ("AUDIT_REPORT.md", "0. Verdict in one paragraph"):
        "The code was solid, but the main result did not hold up. It was fixable, and it was fixed.",
    ("AUDIT_REPORT.md", "Final outcome (after the fixes, October 2026)"):
        "After the fixes the measurement works. The control benefit was tested fairly and not found.",
    ("AUDIT_REPORT.md", "1. What is correct"):
        "What was already right: detection, tracking, the light's safety logic, the tests and the maths.",
    ("AUDIT_REPORT.md", "1.1 The mathematical check you asked for (§23 of your brief) — verified from source"):
        "The \"count gets stuck at full\" argument is correct and is proven by a test.",
    ("AUDIT_REPORT.md", "2. What is wrong (critical — these break the headline claim)"):
        "The problems that made the old headline result invalid.",
    ("AUDIT_REPORT.md", "W1. Open-loop \"throughput / vehicles served\" measures agreement with the REAL signal  ★ most important"):
        "The cars in the video obey the real past light. \"Cars served\" only measures overlap "
        "with that light, not control quality.",
    ("AUDIT_REPORT.md", "W2. The S3 → S4 gain is a weight-renormalisation artefact, not the risk signal"):
        "Switching the new measure off gave the identical result, so the new measure did not cause it.",
    ("AUDIT_REPORT.md", "W3. The real footage almost never enters the saturation regime the story depends on"):
        "The \"box gets full\" situation almost never happens in these videos.",
    ("AUDIT_REPORT.md", "W4. One decision, one clip"):
        "The old improvement came from one single decision in one short video. That is too "
        "little evidence.",
    ("AUDIT_REPORT.md", "W5. `X` and `S` are dominated by measurement noise"):
        "The old queue-reach numbers jumped around because of box wobble and far-away cars "
        "looking slow.",
    ("AUDIT_REPORT.md", "W6. Raza fidelity is a \"Raza-style\" baseline, and the base paper PDF is corrupt"):
        "The baseline is Raza-like, not an exact copy, and the paper file was broken. It has "
        "been replaced and the facts checked.",
    ("AUDIT_REPORT.md", "W7. The camera geometry does not define queues (found during the fixes)"):
        "The road shapes on the image were drawn wrongly, so \"queue reach\" was not really "
        "measuring queues. They were redrawn.",
    ("AUDIT_REPORT.md", "Status of fixes (updated as work proceeds)"):
        "Each problem and where it was fixed.",
    ("AUDIT_REPORT.md", "3. What is missing"):
        "What was missing before the fixes: a fair test, controls, repetition, heavy traffic, a "
        "robust measurement and a better demo.",
    ("AUDIT_REPORT.md", "4. What should be removed / cleaned"):
        "The clutter that was cleaned up and archived.",
    ("AUDIT_REPORT.md", "5. What should be improved"):
        "The improvements, in priority order.",
    ("AUDIT_REPORT.md", "6. Is the enhancement genuinely demonstrated?"):
        "The measurement: yes. Better control: tested, and not found.",
    ("AUDIT_REPORT.md", "7. Is the research story defensible?"):
        "Yes. The chain of reasoning is sound, and the conclusion is now honest.",
    ("AUDIT_REPORT.md", "8. Build plan (executed — see the status table above for where each item landed) (≈ 5 weeks of student time, ≈ 6 phases)"):
        "The plan that was followed to fix the project.",
    # ---------------- PROTOCOL ----------------
    ("sim/PROTOCOL.md", "Question"):
        "Does adding queue reach help when cars actually obey the light?",
    ("sim/PROTOCOL.md", "Fixed design"):
        "The simulated junction, traffic and camera rules were fixed in advance.",
    ("sim/PROTOCOL.md", "Factors"):
        "What was varied: controller, timing rule, how density is scaled, camera noise, and 20 random traffic patterns.",
    ("sim/PROTOCOL.md", "Analysis (pre-specified)"):
        "How the results would be judged was decided before running the test, so it could not be "
        "adjusted afterwards.",
    # ---------------- ADDITIONAL PAPERS ----------------
    ("docs/research/additional_tits_papers.md", "1. Mohajerpoor, Cai & Ramezani (2023) — the closest match to this project's findings"):
        "A newer paper that uses prediction to set green timing, not to choose the road. That "
        "supports my finding that timing is what matters.",
    ("docs/research/additional_tits_papers.md", "2. Zhu et al. (T-ITS, 2024/2025) — queue length from sparse vehicle trajectories"):
        "A newer paper on estimating queues when only some vehicles are seen. That is my "
        "far-away detection problem.",
    ("docs/research/additional_tits_papers.md", "Background works that come up in the same searches (older or not T-ITS)"):
        "Older related methods, useful for background questions.",
    ("docs/research/additional_tits_papers.md", "Should the project be re-based on one of these?"):
        "No. Keep Li and Wei; use these two as supporting references.",
    # ---------------- GLOSSARY ----------------
    ("PROJECT_EXPLAINED.md", "Part 2 — Traffic-engineering terms"):
        "The vocabulary of traffic lights: phases, green time, queues, saturation, spillback, PCE.",
    ("PROJECT_EXPLAINED.md", "Part 3 — Computer-vision terms"):
        "The vocabulary of the camera side: detection (YOLO), tracking (ByteTrack), regions, "
        "perspective, accuracy.",
    ("PROJECT_EXPLAINED.md", "Part 4 — This project's own measures and symbols"):
        "The letters D, Q, A, F, X, S and what each one measures.",
    ("PROJECT_EXPLAINED.md", "Part 5 — Controllers (the \"arms\" of the experiment)"):
        "The ten controller versions that were compared, and what each one tests.",
    ("PROJECT_EXPLAINED.md", "Part 6 — The research papers and publishing terms"):
        "Who wrote what, where it was published, and the maths terms used in those papers.",
    ("PROJECT_EXPLAINED.md", "Optimisation and control terms used in Li and Wei"):
        "The heavy maths machinery of the newer papers, and why it was not used here.",
    ("PROJECT_EXPLAINED.md", "Part 7 — Experiment and statistics terms"):
        "The vocabulary of fair testing: simulation, open vs closed loop, seeds, confidence intervals, controls.",
    ("PROJECT_EXPLAINED.md", "Part 8 — Software and tooling terms"):
        "The software tools used to build and test the project.",
    ("PROJECT_EXPLAINED.md", "Part 9 — Data"):
        "The three Bellevue traffic videos.",
    ("PROJECT_EXPLAINED.md", "Part 10 — What each document in the repository contains"):
        "A map of the separate documents (all included in this one).",
    ("PROJECT_EXPLAINED.md", "Part 11 — Sentences you must be able to say precisely"):
        "Exact wording for the claims that are easiest to get wrong in a viva.",
    # ---------------- VIVA ----------------
    ("docs/VIVA_QA.md", "A. Problem and research chain"):
        "Questions about the problem, the base paper, its weakness, and the newer papers.",
    ("docs/VIVA_QA.md", "B. The enhancement"):
        "Questions about your own idea and how it is computed.",
    ("docs/VIVA_QA.md", "C. Spillback and the open-loop limitation"):
        "Questions about what is and isn't measured, and why recorded video cannot prove improvement.",
    ("docs/VIVA_QA.md", "D. Harder follow-ups a strict examiner may ask"):
        "Tough cross-questions and calm, evidence-based answers.",
    # ---------------- STUDY GUIDE ----------------
    ("docs/STUDY_GUIDE.md", "Stage 1 — The story (day 1)"):
        "Learn to tell the whole story in two minutes.",
    ("docs/STUDY_GUIDE.md", "Stage 2 — The computer-vision pipeline (days 2–3)"):
        "Learn how the camera part works, and the problems you fixed.",
    ("docs/STUDY_GUIDE.md", "Stage 3 — Prediction and the score (day 3)"):
        "Learn to derive the \"count gets stuck\" argument and compute a score by hand.",
    ("docs/STUDY_GUIDE.md", "Stage 4 — The controller and timing (day 4)"):
        "Learn how the light chooses a road and how long the green lasts.",
    ("docs/STUDY_GUIDE.md", "Stage 5 — Why the open-loop result was invalid (day 5)"):
        "Learn why the old +21% was wrong, and how you proved it.",
    ("docs/STUDY_GUIDE.md", "Stage 6 — The closed-loop experiment (day 6)"):
        "Learn the fair test and its four results.",
    ("docs/STUDY_GUIDE.md", "Stage 7 — Rehearse (day 7)"):
        "Practise answers and the presentation.",
    ("docs/STUDY_GUIDE.md", "10-minute presentation outline"):
        "What to say in each minute of the talk.",
    ("docs/STUDY_GUIDE.md", "Live-demo checklist"):
        "What to point at while the demo video plays.",
    ("docs/STUDY_GUIDE.md", "Things to *never* say"):
        "Phrases that overclaim, and what to say instead.",
    ("docs/STUDY_GUIDE.md", "A note on ownership"):
        "Showing you can predict what happens when you change a setting is the strongest proof "
        "that you understand the project.",
}

SIMPLE_DEFAULT_SKIP = {"A note on ownership"}


def note(text: str) -> str:
    return f"\n> 💡 **In simple terms:** {text}\n"


def extract(path: str, mode: str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    if mode.startswith("from:"):
        marker = mode[len("from:"):]
        index = text.index(marker)
        text = text[index:]
    else:
        # drop the document's own top-level title (first line starting with "# ")
        lines = text.splitlines()
        if lines and lines[0].startswith("# "):
            lines = lines[1:]
        text = "\n".join(lines)
    return text.strip() + "\n"


def demote_and_annotate(source: str, body: str, used: set) -> str:
    """Demote headings by one level and add the simple-terms note at the END of each
    annotated section (just before the next heading of the same or higher level)."""
    out_lines: list[str] = []
    pending: list[tuple[int, str]] = []  # stack of (level, note text) waiting for section end
    in_code = False

    def flush(level: int) -> None:
        while pending and pending[-1][0] >= level:
            _, text = pending.pop()
            out_lines.append(note(text))

    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        match = None if in_code else re.match(r"^(#{1,5}) (.*)$", line)
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            flush(level)
            out_lines.append("#" * min(level + 1, 6) + " " + heading)
            key = (source, heading)
            if key in SIMPLE:
                pending.append((level, SIMPLE[key]))
                used.add(key)
            continue
        out_lines.append(line)
    flush(1)
    return "\n".join(out_lines) + "\n"


def main() -> None:
    used: set = set()
    toc = ["## Contents", ""]
    sections = []
    for number, (title, source, mode) in enumerate(PARTS, 1):
        anchor = f"part-{number}"
        toc.append(f"{number}. [{title}](#{anchor}) — from `{source}`")
        body = demote_and_annotate(source, extract(source, mode), used)
        sections.append(
            f'\n---\n\n<a id="{anchor}"></a>\n# Part {number} — {title}\n\n'
            f"*Source: `{source}`*\n\n{body}"
        )
    header = [
        "# Complete Project Document",
        "",
        "**Vision-Measured Spatial Queue Reach for Adaptive Traffic Signal Control**",
        "",
        "Every project document in one file: overview, final report, architecture, technical",
        "guide, audit, experiment protocol, papers, glossary, viva answers and study plan.",
        "After every part there is a short **💡 In simple terms** box that says what the",
        "part means in plain language.",
        "",
        "This file is generated by `python scripts/build_complete_doc.py` from the separate",
        "documents, so it always matches them. Relative links (e.g. `report/...`) point to",
        "files in the repository.",
        "",
        "> 💡 **The whole project in simple terms:** A camera-based traffic light (Raza 2025) only",
        "> counts the cars that are there now. Inspired by two newer papers (Li 2025: queue",
        "> profiles; Wei 2025: predicting queues), I measured with the camera how far back each",
        "> queue reaches and predicted it a few seconds ahead. I made that measurement work",
        "> reliably, found and proved that an earlier \"+21%\" result was a mistake, and ran a fair",
        "> simulation test (7,920 runs) where cars obey the light. The measurement works, but it",
        "> does not change which road gets green, and I explain why. What really reduces waiting",
        "> is ending each green once its queue has cleared.",
        "",
    ]
    OUT.write_text("\n".join(header + toc) + "\n" + "".join(sections), encoding="utf-8")
    missing = [k for k in SIMPLE if k not in used]
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    if missing:
        print("notes whose heading was not found:")
        for k in missing:
            print("  ", k)


if __name__ == "__main__":
    main()

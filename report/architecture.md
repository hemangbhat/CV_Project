# Architecture

Pipeline stages, from video input to evaluation (Requirement 17.2).

```mermaid
flowchart TD
    A[Junction video<br/>videos/*.mp4] --> B[Video_Ingestor<br/>src/video_io.py]
    C[Configuration<br/>config/default.json] --> D[Config_Loader<br/>src/config.py]
    B -->|frame, frame_index| E[Detector<br/>YOLO, src/detection.py]
    E -->|detections| F[Tracker<br/>ByteTrack, src/tracking.py]
    F -->|tracks with Track_IDs| G[Approach_Assigner<br/>src/lane_analysis.py]
    D -->|ROI polygons, queue regions| G
    G -->|assigned tracks| H[Metrics_Engine<br/>src/traffic_metrics.py]
    H -->|density, queue length| I[Score_Calculator<br/>Score = a*D + '1-a'*Q]
    D -->|alpha| I
    I -->|four scores| J[Controller<br/>fixed-time or adaptive]
    J -->|approach, green time| K[Phase_Sequencer<br/>src/signal_controller.py]
    K -->|signal states| H
    K --> L[Signal_Overlay<br/>src/overlay.py]
    H --> L
    L --> M[Annotated video<br/>results/videos/]
    H --> N[Results_Store<br/>src/results_store.py]
    K --> N
    N --> O[Run_Logs<br/>results/run_logs/]
    O --> P[Evaluation_Harness<br/>src/evaluation.py]
    P --> Q[Tables<br/>results/tables/]
    P --> R[Graphs<br/>results/graphs/]
    Q --> S[report/]
    R --> S
    M --> S
```

The only feedback edge is Phase_Sequencer back into Metrics_Engine: Waiting_Time is
held while an approach is GREEN, so the measurement stage needs the signal state in
force on the frame it is measuring. Everything else flows forward.

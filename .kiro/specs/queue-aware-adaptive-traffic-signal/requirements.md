# Requirements Document

## Introduction

This feature delivers a vision-based adaptive traffic signal control system titled **"Vision-Based Adaptive Traffic Signal Control Using Vehicle Detection, Tracking and Queue-Aware Traffic Estimation"**.

The system reads a traffic-junction video, detects and tracks vehicles, assigns each tracked vehicle to one of four junction approaches (North, East, South, West), estimates per-approach Vehicle Density and Queue Length, combines the two estimates into a single per-approach Score, and uses that Score to allocate green time. A fixed-time controller is implemented as a baseline so that the adaptive controller can be measured against it on the same videos.

The research question the system answers is: **can tracked queue information improve adaptive signal allocation beyond using traffic density alone?** The project contribution is the queue-aware scoring function

```
Score_i = alpha * D_i + (1 - alpha) * Q_i
```

where `D_i` is normalized density and `Q_i` is normalized queue length for approach `i`, and `alpha` is a tunable experimental parameter. Novelty is framed as a practical queue-aware extension and its measured evaluation, not as a globally novel algorithm.

No physical traffic hardware is involved. Signal state is drawn on screen as a simulated traffic light overlay. The deliverables serve a university course project with a written report and a viva, so measured result graphs, comparison tables, screenshots, and an architecture flowchart are part of the scope.

**Fixed technology stack:** Python, OpenCV, Ultralytics YOLO (YOLO11 or a comparable practical pretrained model), ByteTrack, NumPy, Matplotlib. No other runtime libraries are introduced.

**Processing pipeline:**

```
Traffic video
  -> OpenCV video processing
  -> Vehicle detection (YOLO)
  -> Vehicle tracking (ByteTrack)
  -> Approach/lane assignment via per-approach ROI polygons
  -> per-approach Vehicle Density + Queue Length
  -> Score / Priority
  -> Adaptive signal logic
  -> Green time
  -> On-screen simulated traffic-light output
  -> Performance comparison (adaptive vs fixed-time)
```

## Out of Scope

The following are explicitly excluded from this feature. The system does not implement, depend on, or claim any of them:

- Reinforcement learning of any kind, including DQN, multi-agent reinforcement learning, and policy-gradient control
- Graph convolutional networks (GCN) and other graph-neural traffic prediction
- Jetson or other edge-hardware deployment, and IoT sensor nodes
- SUMO (or any microscopic traffic simulator) as the primary system; the primary system is video-driven
- GA-SGD or other evolutionary hyperparameter optimization
- A custom YOLO-LIGHT style architecture or any custom detector training
- Emergency-vehicle detection and priority pre-emption
- License-plate recognition
- Drone or UAV based monitoring
- Control of real traffic-signal hardware
- Large-scale dataset construction or public dataset release

## Glossary

- **System**: The complete video-based adaptive traffic signal control application defined by this document.
- **Video_Ingestor**: The component that opens a video file with OpenCV, reads frames in order, and exposes frame index and video frame rate.
- **Detector**: The component that runs the pretrained Ultralytics YOLO model on a frame and returns bounding boxes with a Vehicle_Class and confidence for each detection.
- **Tracker**: The component that applies ByteTrack to per-frame detections and returns tracks with persistent Track_IDs.
- **Approach_Assigner**: The component that assigns each track to an Approach using ROI_Polygons, and determines whether the track lies inside that Approach's Queue_Region.
- **Metrics_Engine**: The component that computes per-approach Vehicle_Count, Vehicle_Density, Queue_Length, and Waiting_Time from tracked data.
- **Score_Calculator**: The component that computes the per-approach Score from Vehicle_Density and Queue_Length using the tunable weight Alpha.
- **Adaptive_Controller**: The proposed queue-aware controller that selects the served Approach and assigns Green_Time from Score values.
- **Fixed_Time_Controller**: The baseline controller that serves the four Approaches in round-robin order with a constant Green_Time of 30 seconds each.
- **Signal_Overlay**: The component that draws ROI_Polygons, Queue_Regions, tracks, per-approach measurements, and the simulated traffic-light state onto output frames.
- **Evaluation_Harness**: The component that runs both controllers over the same videos, records Evaluation_Metrics, and writes result tables and graphs.
- **Config_Loader**: The component that loads and validates tunable parameters and ROI geometry from a configuration file.
- **Results_Store**: The component that serializes per-run measurements and Evaluation_Metrics to files under `results/` and reads them back for graph and table generation.
- **Approach**: One of the four junction legs, identified as North, East, South, or West.
- **ROI_Polygon**: A closed polygon in image coordinates delimiting one Approach's observed road area.
- **Queue_Region**: A sub-region polygon inside an ROI_Polygon representing the stop-line waiting area of that Approach.
- **Vehicle_Class**: One of car, motorcycle, bus, truck.
- **Track_ID**: The integer identifier ByteTrack assigns to a tracked vehicle, persistent across frames for the same vehicle.
- **Vehicle_Count**: The number of distinct Track_IDs whose reference point lies inside an Approach's ROI_Polygon in the current frame.
- **Vehicle_Density**: `D_i`, the Vehicle_Count of Approach `i` divided by that Approach's configured Saturation_Count, clamped to the range 0 to 1 inclusive.
- **Queue_Length**: The number of distinct Track_IDs whose reference point lies inside an Approach's Queue_Region in the current frame.
- **Normalized_Queue**: `Q_i`, the Queue_Length of Approach `i` divided by that Approach's configured Queue_Capacity, clamped to the range 0 to 1 inclusive.
- **Alpha**: The configurable weight in the range 0 to 1 inclusive that balances Vehicle_Density against Normalized_Queue in the Score.
- **Score**: `Score_i = Alpha * D_i + (1 - Alpha) * Q_i` for Approach `i`, a value in the range 0 to 1 inclusive.
- **Green_Time**: The duration in simulated seconds for which one Approach holds the green state.
- **Signal_State**: The state of one Approach at a point in simulated time, one of GREEN, YELLOW, RED.
- **Cycle**: One pass of the controller in which it selects and serves one Approach for its Green_Time followed by the Yellow_Duration.
- **Yellow_Duration**: The configurable duration in simulated seconds of the YELLOW state that follows every GREEN state.
- **Simulated_Clock**: The elapsed video time in seconds, computed as the processed frame index divided by the video frame rate.
- **Starvation_Limit**: The configurable maximum number of consecutive Cycles for which an Approach may remain unselected.
- **Waiting_Time**: For one Track_ID, the accumulated simulated seconds during which the track is inside a Queue_Region while its Approach Signal_State is not GREEN.
- **Evaluation_Metrics**: The measured set comprising average Waiting_Time, average Queue_Length, maximum Queue_Length, vehicles served, throughput, and processing frame rate.
- **Vehicles_Served**: The count of distinct Track_IDs that leave a Queue_Region while their Approach Signal_State is GREEN during a run.
- **Throughput**: Vehicles_Served divided by the total simulated duration of the run, expressed in vehicles per minute.
- **Processing_FPS**: Frames processed per wall-clock second during a run, recorded for the adaptive pipeline.
- **Run_Log**: The machine-readable per-run record written by Results_Store containing configuration, per-frame measurements, and Evaluation_Metrics.

## Requirements

### Requirement 1: Video Ingestion and Playback

**User Story:** As a developer, I want the system to read and display a traffic-junction video with OpenCV, so that I have a verified input stage before adding detection.

#### Acceptance Criteria

1. WHEN a path to a readable video file is supplied, THE Video_Ingestor SHALL open the video and return its frame width, frame height, frame count, and frame rate.
2. WHEN the Video_Ingestor reads frames from an opened video, THE Video_Ingestor SHALL return frames in ascending frame-index order starting at index 0 with no index skipped.
3. WHERE display mode is enabled, THE Video_Ingestor SHALL render each read frame in an OpenCV window and SHALL close the window when the final frame has been read.
4. IF the supplied path does not exist or the file cannot be decoded by OpenCV, THEN THE Video_Ingestor SHALL raise an error naming the supplied path and SHALL exit with a non-zero status code.
5. IF the reported frame rate of the opened video is less than or equal to 0, THEN THE Video_Ingestor SHALL substitute the configured Default_Frame_Rate and SHALL record a warning in the Run_Log.
6. WHEN the user presses the configured quit key during display mode, THE Video_Ingestor SHALL stop reading frames and SHALL release the video capture and all windows.

### Requirement 2: Vehicle Detection

**User Story:** As a developer, I want YOLO to detect cars, motorcycles, buses, and trucks in each frame, so that traffic can be measured from the video.

#### Acceptance Criteria

1. WHEN a frame is submitted to the Detector, THE Detector SHALL return zero or more detections, each carrying an axis-aligned bounding box in image coordinates, a Vehicle_Class, and a confidence value in the range 0 to 1 inclusive.
2. THE Detector SHALL restrict returned detections to the Vehicle_Classes car, motorcycle, bus, and truck.
3. WHEN a detection has a confidence value below the configured Confidence_Threshold, THE Detector SHALL exclude that detection from the returned detections.
4. THE Detector SHALL load the pretrained Ultralytics YOLO weights identified by the configured Model_Path without performing any training.
5. WHEN a bounding box is returned, THE Detector SHALL constrain the box coordinates to lie within the frame bounds and SHALL produce a box with width greater than 0 and height greater than 0.
6. IF the configured Model_Path cannot be loaded, THEN THE Detector SHALL raise an error naming the configured Model_Path and SHALL exit with a non-zero status code.
7. WHEN detection is run over an input video with annotation output enabled, THE System SHALL write an annotated output video to `results/videos/` containing one output frame per processed input frame, with each detection drawn as a labelled bounding box.

### Requirement 3: Vehicle Tracking with Persistent Identities

**User Story:** As a developer, I want ByteTrack to assign persistent IDs to detected vehicles, so that the same vehicle is counted once and its queue behaviour can be followed over time.

#### Acceptance Criteria

1. WHEN per-frame detections are submitted to the Tracker, THE Tracker SHALL return tracks, each carrying a Track_ID, a bounding box, and a Vehicle_Class.
2. THE Tracker SHALL assign distinct Track_IDs to distinct tracks within a single frame.
3. WHILE a vehicle remains detected across consecutive frames, THE Tracker SHALL report the same Track_ID for that vehicle.
4. WHEN a Track_ID has been absent for more than the configured Track_Buffer number of frames, THE Tracker SHALL retire that Track_ID and SHALL leave the retired Track_ID unassigned to any later track.
5. THE Tracker SHALL maintain, for each active Track_ID, an ordered trajectory of at most the configured Trajectory_Length most recent reference points.
6. WHEN tracking visualization is enabled, THE Signal_Overlay SHALL draw each active track's Track_ID, Vehicle_Class, and trajectory polyline on the output frame.

### Requirement 4: Approach Assignment via ROI Polygons

**User Story:** As a developer, I want each tracked vehicle assigned to one junction approach and marked as queueing or not, so that traffic can be measured per approach.

#### Acceptance Criteria

1. THE Config_Loader SHALL load exactly four ROI_Polygons, one for each Approach in the set North, East, South, West, and exactly one Queue_Region for each Approach.
2. WHEN a track is submitted to the Approach_Assigner, THE Approach_Assigner SHALL compute the track's reference point as the midpoint of the bottom edge of the track bounding box.
3. WHEN a track's reference point lies inside exactly one ROI_Polygon, THE Approach_Assigner SHALL assign that track to the corresponding Approach.
4. IF a track's reference point lies inside two or more ROI_Polygons, THEN THE Approach_Assigner SHALL assign that track to the Approach whose ROI_Polygon centroid is nearest to the reference point and SHALL record the overlap event in the Run_Log.
5. IF a track's reference point lies outside every ROI_Polygon, THEN THE Approach_Assigner SHALL mark that track as unassigned and SHALL exclude that track from every per-approach measurement.
6. WHEN a track is assigned to an Approach, THE Approach_Assigner SHALL mark the track as queueing if and only if the track's reference point lies inside that Approach's Queue_Region.
7. IF a configured Queue_Region contains any vertex outside its parent ROI_Polygon, THEN THE Config_Loader SHALL reject the configuration and SHALL report the offending Approach and vertex.
8. IF a configured ROI_Polygon has fewer than 3 vertices or any vertex outside the video frame bounds, THEN THE Config_Loader SHALL reject the configuration and SHALL report the offending Approach.

### Requirement 5: Per-Approach Traffic Measurement

**User Story:** As a researcher, I want per-approach counts, density, queue length, and waiting time, so that I can quantify the traffic condition of each approach.

#### Acceptance Criteria

1. WHEN a frame has been tracked and assigned, THE Metrics_Engine SHALL compute the Vehicle_Count of each Approach as the number of distinct Track_IDs assigned to that Approach in that frame.
2. THE Metrics_Engine SHALL compute Vehicle_Density for each Approach as the Approach Vehicle_Count divided by the configured Saturation_Count of that Approach, clamped to the range 0 to 1 inclusive.
3. THE Metrics_Engine SHALL compute Queue_Length for each Approach as the number of distinct Track_IDs assigned to that Approach and marked as queueing in that frame.
4. THE Metrics_Engine SHALL compute Normalized_Queue for each Approach as the Approach Queue_Length divided by the configured Queue_Capacity of that Approach, clamped to the range 0 to 1 inclusive.
5. WHILE a Track_ID is marked as queueing and the Signal_State of its Approach is not GREEN, THE Metrics_Engine SHALL increase that Track_ID's Waiting_Time by the simulated duration of one frame.
6. WHILE the Signal_State of an Approach is GREEN, THE Metrics_Engine SHALL hold the Waiting_Time of every Track_ID assigned to that Approach unchanged.
7. THE Metrics_Engine SHALL report Vehicle_Count and Queue_Length as integers greater than or equal to 0, and Vehicle_Density and Normalized_Queue as values in the range 0 to 1 inclusive.
8. THE Metrics_Engine SHALL compute Queue_Length as a value less than or equal to the Vehicle_Count of the same Approach in the same frame.
9. WHERE per-class weighting is enabled, THE Metrics_Engine SHALL compute Vehicle_Density from the sum of configured passenger-car-equivalent weights of the assigned tracks instead of the raw Vehicle_Count, using the same Saturation_Count divisor and the same clamping.
10. IF the configured Saturation_Count or Queue_Capacity of any Approach is less than or equal to 0, THEN THE Config_Loader SHALL reject the configuration and SHALL report the offending Approach and parameter name.

### Requirement 6: Queue-Aware Score Computation

**User Story:** As a researcher, I want a single tunable score per approach that combines density and queue length, so that the controller has one comparable priority value per approach.

#### Acceptance Criteria

1. THE Score_Calculator SHALL compute the Score of each Approach as `Alpha * Vehicle_Density + (1 - Alpha) * Normalized_Queue`.
2. THE Score_Calculator SHALL produce a Score in the range 0 to 1 inclusive for every Approach.
3. WHEN Alpha is 1, THE Score_Calculator SHALL produce a Score equal to the Vehicle_Density of the same Approach.
4. WHEN Alpha is 0, THE Score_Calculator SHALL produce a Score equal to the Normalized_Queue of the same Approach.
5. WHEN the Normalized_Queue of an Approach increases while Alpha is less than 1 and Vehicle_Density is unchanged, THE Score_Calculator SHALL produce a Score greater than or equal to the previous Score of that Approach.
6. WHEN the Vehicle_Density of an Approach increases while Alpha is greater than 0 and Normalized_Queue is unchanged, THE Score_Calculator SHALL produce a Score greater than or equal to the previous Score of that Approach.
7. THE Config_Loader SHALL accept Alpha values in the range 0 to 1 inclusive and SHALL reject any other Alpha value with an error naming the supplied value.
8. THE Score_Calculator SHALL treat Alpha, Saturation_Count, Queue_Capacity, and the Green_Time thresholds as configurable experimental parameters read from configuration rather than as constants embedded in code.

### Requirement 7: Fixed-Time Baseline Controller

**User Story:** As a researcher, I want a fixed-time controller as the baseline, so that the adaptive controller has a comparable reference on the same videos.

#### Acceptance Criteria

1. THE Fixed_Time_Controller SHALL serve the Approaches in the repeating order North, East, South, West.
2. WHEN the Fixed_Time_Controller starts a Cycle, THE Fixed_Time_Controller SHALL assign a Green_Time of 30 simulated seconds to the selected Approach.
3. THE Fixed_Time_Controller SHALL assign the same Green_Time to every Cycle irrespective of Vehicle_Density, Queue_Length, and Score values.
4. WHEN a Green_Time elapses, THE Fixed_Time_Controller SHALL set the served Approach Signal_State to YELLOW for the configured Yellow_Duration and then to RED.
5. WHEN the Approach served in the previous Cycle was West, THE Fixed_Time_Controller SHALL select North for the next Cycle.

### Requirement 8: Adaptive Queue-Aware Controller

**User Story:** As a researcher, I want the controller to give green time to the approach with the highest queue-aware score, so that waiting and queue length are reduced compared with fixed timing.

#### Acceptance Criteria

1. WHEN the Adaptive_Controller starts a Cycle, THE Adaptive_Controller SHALL read the current Score of all four Approaches.
2. WHEN no Approach has reached the Starvation_Limit, THE Adaptive_Controller SHALL select an Approach whose Score is greater than or equal to the Score of every other Approach.
3. IF two or more Approaches hold the maximal Score, THEN THE Adaptive_Controller SHALL select among them the Approach that has waited the greatest number of Cycles, and SHALL break a remaining tie using the fixed order North, East, South, West.
4. WHEN the selected Approach has a Score below 0.3, THE Adaptive_Controller SHALL assign a Green_Time of 30 simulated seconds.
5. WHEN the selected Approach has a Score greater than or equal to 0.3 and below 0.6, THE Adaptive_Controller SHALL assign a Green_Time of 45 simulated seconds.
6. WHEN the selected Approach has a Score greater than or equal to 0.6, THE Adaptive_Controller SHALL assign a Green_Time of 60 simulated seconds.
7. THE Adaptive_Controller SHALL assign a Green_Time that is greater than or equal to the Green_Time it assigns for any lower Score value.
8. THE Adaptive_Controller SHALL assign a Green_Time within the configured range bounded below by Min_Green_Time and above by Max_Green_Time.
9. THE Adaptive_Controller SHALL read the Score thresholds and their associated Green_Time values from configuration.
10. WHEN a Green_Time elapses, THE Adaptive_Controller SHALL set the served Approach Signal_State to YELLOW for the configured Yellow_Duration and then to RED before starting the next Cycle.

### Requirement 9: Starvation Prevention

**User Story:** As a researcher, I want every approach to be served within a bounded number of cycles, so that a low-score approach is not skipped indefinitely.

#### Acceptance Criteria

1. THE Adaptive_Controller SHALL maintain for each Approach a Cycles_Waited counter recording the number of consecutive Cycles since that Approach was last selected.
2. WHEN an Approach is selected for a Cycle, THE Adaptive_Controller SHALL reset the Cycles_Waited counter of that Approach to 0 and SHALL increase the Cycles_Waited counter of every other Approach by 1.
3. WHEN one or more Approaches have a Cycles_Waited counter greater than or equal to the Starvation_Limit, THE Adaptive_Controller SHALL select the Approach with the greatest Cycles_Waited counter irrespective of Score values.
4. THE Adaptive_Controller SHALL keep the Cycles_Waited counter of every Approach less than or equal to the Starvation_Limit at the end of every Cycle.
5. THE Adaptive_Controller SHALL select every Approach at least once within any window of consecutive Cycles whose length is Starvation_Limit plus 1.
6. WHEN an Approach is selected because its Cycles_Waited counter reached the Starvation_Limit, THE Adaptive_Controller SHALL assign the Green_Time that corresponds to that Approach's current Score and SHALL record the starvation override in the Run_Log.
7. THE Config_Loader SHALL accept a Starvation_Limit that is an integer greater than or equal to 1 and SHALL reject any other value with an error naming the supplied value.

### Requirement 10: Signal State Integrity

**User Story:** As a reviewer, I want the simulated signal to be safe and unambiguous at all times, so that the reported results describe a valid control policy.

#### Acceptance Criteria

1. THE System SHALL hold exactly one Approach in the GREEN Signal_State at every point of the Simulated_Clock after the first Cycle has started.
2. WHILE one Approach holds the GREEN Signal_State, THE System SHALL hold the other three Approaches in the RED Signal_State.
3. WHILE one Approach holds the YELLOW Signal_State, THE System SHALL hold the other three Approaches in the RED Signal_State.
4. THE System SHALL transition an Approach Signal_State only along the sequence RED to GREEN, GREEN to YELLOW, and YELLOW to RED.
5. WHEN the System starts a run and before the first Cycle begins, THE System SHALL hold all four Approaches in the RED Signal_State.
6. THE System SHALL apply the state-integrity criteria of this requirement to both the Fixed_Time_Controller and the Adaptive_Controller.

### Requirement 11: Simulated Clock and Phase Timing

**User Story:** As a developer, I want green time expressed in simulated seconds derived from the video, so that timings are comparable across controllers and runs.

#### Acceptance Criteria

1. THE System SHALL compute the Simulated_Clock as the processed frame index divided by the video frame rate.
2. WHEN a Green_Time of `g` simulated seconds is assigned, THE System SHALL hold the GREEN Signal_State for the number of frames equal to `g` multiplied by the video frame rate, rounded to the nearest integer and at minimum 1 frame.
3. THE System SHALL advance the Simulated_Clock monotonically over a run.
4. WHEN the video reaches its final frame during a phase, THE System SHALL end the run, SHALL mark the truncated phase in the Run_Log, and SHALL exclude the truncated phase from per-phase averages.

### Requirement 12: On-Screen Visualization

**User Story:** As a student presenting the project, I want a live overlay showing measurements and the simulated signal, so that the behaviour is visible in the demo and in screenshots.

#### Acceptance Criteria

1. WHEN a frame has been processed, THE Signal_Overlay SHALL draw the four ROI_Polygons and the four Queue_Regions with a per-Approach label on that frame.
2. WHEN a frame has been processed, THE Signal_Overlay SHALL draw for each Approach the Approach name, Vehicle_Count, Queue_Length, and Score rounded to 2 decimal places.
3. WHEN a frame has been processed, THE Signal_Overlay SHALL draw a simulated traffic light for each Approach coloured according to that Approach's current Signal_State.
4. WHILE an Approach holds the GREEN Signal_State, THE Signal_Overlay SHALL display that Approach name, the assigned Green_Time in seconds, and the remaining seconds of the phase.
5. THE Signal_Overlay SHALL draw the active controller name and the current Simulated_Clock value on every output frame.
6. WHERE annotated video output is enabled, THE Signal_Overlay SHALL write every drawn frame to a video file under `results/videos/` at the input video frame rate.
7. WHEN the Approach with the greatest Score changes between Cycles, THE Signal_Overlay SHALL display the newly selected Approach name together with the Score that produced the selection.

### Requirement 13: Measured Evaluation of Adaptive Against Fixed-Time

**User Story:** As a researcher, I want both controllers measured on the same videos, so that I can report an evidence-based comparison in the report and viva.

#### Acceptance Criteria

1. WHEN an evaluation is requested for a video, THE Evaluation_Harness SHALL run the Fixed_Time_Controller and the Adaptive_Controller over that same video using the same ROI_Polygons, Queue_Regions, Detector configuration, and Tracker configuration.
2. WHEN a run completes, THE Evaluation_Harness SHALL record the average Waiting_Time, the average Queue_Length, the maximum Queue_Length, Vehicles_Served, and Throughput for that run.
3. WHEN a run of the Adaptive_Controller completes, THE Evaluation_Harness SHALL record the Processing_FPS of that run.
4. THE Evaluation_Harness SHALL derive every reported value in Evaluation_Metrics from measurements taken during a completed run recorded in a Run_Log.
5. THE Evaluation_Harness SHALL report Evaluation_Metrics per Approach and aggregated over all four Approaches.
6. WHEN evaluation over all configured videos completes, THE Evaluation_Harness SHALL write a comparison table to `results/tables/` containing one row per video and controller pair and one column per metric in Evaluation_Metrics.
7. WHEN evaluation over all configured videos completes, THE Evaluation_Harness SHALL write Matplotlib graphs to `results/graphs/` comparing the two controllers for average Waiting_Time, average Queue_Length, maximum Queue_Length, and Throughput.
8. IF a run terminates before the final frame of the video is processed, THEN THE Evaluation_Harness SHALL mark that run as incomplete in the comparison table and SHALL exclude that run from the controller comparison.
9. WHERE an Alpha sweep is configured, THE Evaluation_Harness SHALL run the Adaptive_Controller once per configured Alpha value and SHALL record Evaluation_Metrics separately for each Alpha value.

### Requirement 14: Configuration and Results Persistence

**User Story:** As a developer, I want parameters and results stored in files, so that experiments are reproducible and graphs can be regenerated without reprocessing video.

#### Acceptance Criteria

1. THE Config_Loader SHALL read Alpha, Confidence_Threshold, Model_Path, Track_Buffer, Trajectory_Length, per-Approach Saturation_Count, per-Approach Queue_Capacity, Score thresholds with their Green_Time values, Min_Green_Time, Max_Green_Time, Yellow_Duration, Starvation_Limit, Default_Frame_Rate, and per-Approach ROI_Polygon and Queue_Region vertices from a single configuration file.
2. IF a required configuration parameter is absent, THEN THE Config_Loader SHALL raise an error naming the absent parameter and SHALL exit with a non-zero status code.
3. WHEN a run starts, THE Results_Store SHALL write the complete resolved configuration, the input video path, and the controller name into the Run_Log for that run.
4. FOR ALL Run_Logs written by the Results_Store, reading the Run_Log back SHALL produce configuration values and Evaluation_Metrics equal to those written, within a tolerance of 0.001 for values represented as decimals.
5. FOR ALL configuration files accepted by the Config_Loader, serializing the loaded configuration and loading the serialized result SHALL produce a configuration equal to the loaded configuration.
6. THE Results_Store SHALL write per-frame Vehicle_Count, Queue_Length, Score, and Signal_State for each Approach to a per-run record under `results/`.
7. WHEN graph generation is requested with an existing Run_Log, THE Evaluation_Harness SHALL generate the graphs and tables from that Run_Log without reprocessing the input video.

### Requirement 15: Incremental Verifiable Milestones

**User Story:** As a student on a course timeline, I want the system built as a sequence of independently runnable stages, so that progress can be demonstrated at each step.

#### Acceptance Criteria

1. THE System SHALL provide a runnable entry point that plays an input video with OpenCV and reports frame count and frame rate.
2. THE System SHALL provide a runnable entry point that detects the four Vehicle_Classes and writes an annotated output video to `results/videos/`.
3. THE System SHALL provide a runnable entry point that adds ByteTrack Track_IDs and trajectory visualization to the annotated output video.
4. THE System SHALL provide a runnable entry point that displays live per-Approach Vehicle_Count, Queue_Length, Vehicle_Density, and Score with ROI_Polygons and Queue_Regions drawn.
5. THE System SHALL provide a runnable entry point that selects the controller by command-line argument between the Fixed_Time_Controller and the Adaptive_Controller and renders the simulated signal overlay.
6. THE System SHALL provide a runnable entry point that executes the evaluation experiment and writes the graphs and tables to `results/graphs/` and `results/tables/`.
7. THE System SHALL organize source files as `src/detection.py`, `src/tracking.py`, `src/lane_analysis.py`, `src/traffic_metrics.py`, `src/signal_controller.py`, and `src/main.py`, with data under `data/raw/`, `data/processed/`, and `data/annotations/`, input videos under `videos/`, model weights under `models/`, outputs under `results/videos/`, `results/graphs/`, and `results/tables/`, exploratory work under `notebooks/`, report material under `report/`, and a `README.md` at the project root.
8. THE System SHALL depend at runtime only on Python, OpenCV, Ultralytics YOLO, ByteTrack, NumPy, and Matplotlib.

### Requirement 16: Evaluation Data Set

**User Story:** As a researcher, I want a small, well-chosen set of junction videos, so that evaluation is meaningful without building a large dataset.

#### Acceptance Criteria

1. THE System SHALL accept between 2 and 5 traffic-junction videos as its evaluation input set.
2. THE System SHALL record for each input video the frame rate, resolution, duration, and the Approaches visible in the frame in `data/annotations/`.
3. THE System SHALL designate each input video as either a development video or a final evaluation video and SHALL record that designation in `data/annotations/`.
4. WHEN the Evaluation_Harness reports the final comparison, THE Evaluation_Harness SHALL report results for the final evaluation videos separately from the development videos.
5. IF an input video changes camera viewpoint such that a configured ROI_Polygon no longer covers its Approach road area, THEN THE System SHALL report that the configuration does not match the video and SHALL exclude that video from the evaluation input set.

### Requirement 17: Report Deliverables

**User Story:** As a student submitting a course project, I want the report artefacts produced from measured runs, so that the written report and viva are supported by evidence.

#### Acceptance Criteria

1. WHEN the evaluation experiment completes, THE System SHALL produce under `report/` the comparison graphs, the comparison tables, and demonstration screenshots taken from annotated output frames.
2. THE System SHALL produce under `report/` an architecture diagram showing the pipeline stages from video input through detection, tracking, approach assignment, measurement, scoring, control, and visualization to evaluation.
3. THE System SHALL produce under `report/` a literature-comparison table covering Raza 2025, Saraff 2025, YOLO-LIGHT 2026, Jin 2024, and Lamrabet 2026, with one column stating how this System differs from each work.
4. THE System SHALL state in `report/` and `README.md` that the contribution is a practical queue-aware extension and its measured evaluation rather than a globally novel algorithm.
5. THE System SHALL state in `report/` and `README.md` the items listed in the Out of Scope section of this document as excluded from the project.
6. WHEN a numeric result appears in `report/`, THE System SHALL reference the Run_Log from which that result was measured.

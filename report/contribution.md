## Contribution

The contribution of this project is a **practical queue-aware extension** to
vision-based adaptive signal allocation, together with its **measured evaluation**
against a fixed-time baseline on the same junction videos. It is not a globally
novel algorithm, and no claim of one is made.

Concretely, the per-approach priority is

    Score_i = alpha * D_i + (1 - alpha) * Q_i

where `D_i` is normalized vehicle density and `Q_i` is normalized queue length,
both measured from tracked vehicles, and `alpha` is a configured experimental
weight. Setting `alpha = 1` reduces the System to density-only allocation, which is
what makes the queue term's effect measurable rather than assumed: the same
pipeline, the same videos, and the same detector and tracker configuration are used
for every run, and only the controller and `alpha` differ.

Every number reported in this directory is derived from a Run_Log written under
`results/run_logs/` and is tagged with the `run_id` it was measured from. Nothing is
estimated, extrapolated, or carried over from a run that did not complete.

## Out of Scope

The following are explicitly excluded. This project does not implement, depend on,
or claim any of them:

- Reinforcement learning of any kind, including DQN, multi-agent RL, and
  policy-gradient control
- Graph convolutional networks and other graph-neural traffic prediction
- Jetson or other edge-hardware deployment, and IoT sensor nodes
- SUMO or any microscopic traffic simulator as the primary system; the primary
  system is video-driven
- GA-SGD or other evolutionary hyperparameter optimization
- A custom YOLO-LIGHT style architecture, or any custom detector training
- Emergency-vehicle detection and priority pre-emption
- License-plate recognition
- Drone or UAV based monitoring
- Control of real traffic-signal hardware
- Large-scale dataset construction or public dataset release

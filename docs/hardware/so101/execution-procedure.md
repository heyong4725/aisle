# SO-101 hardware phase: exact execution procedure (HWP-20, SPEC 520)

Blocker: no SO-101 station is available. Every step below is `pending`
until measured on the pinned station; `harness hardware dry-run` prints the
checklist with live statuses and refuses to mark a later step passed while
an earlier one is not.

| step | what | passes when | artifact |
|---|---|---|---|
| 1 | acquire and inventory the station | `station-manifest.json` validates with no `hardware_pending` field (HWP-1) | `docs/hardware/so101/station-manifest.template.json` filled |
| 2 | inspect and anchor the workspace; verify the physical estop | anchoring and estop rows signed in the safety case (HWP-9) | `safety-case.template.json` filled and signed |
| 3 | pin firmware, software, power, USB topology | hashes present in the station manifest | station manifest |
| 4 | measure motor, workspace, camera, and time calibration; verify independently | motor artifact (HWP-2) and workspace artifact (HWP-3) validate; raw records regenerate them (HWP-4) | `calibration-procedure.md` outputs |
| 5 | sign the safety case and preflight checklist | every preflight item `passed`; three roles named (HWP-9) | safety case |
| 6 | no-load and representative-load limit checks | drill records retained with traces and final state (HWP-10) | drill records |
| 7 | watchdog, lease, held-command, estop, power-loss, evidence-sink drills | each drill retained with operator action and stop latency (HWP-10) | drill records |
| 8 | freeze the physical scorer and measure fidelity | scorer contract (HWP-13) plus independent audit estimate | `scorer-contract.json` |
| 9 | freeze reset and intervention protocols | `reset-intervention-protocol.json` frozen (HWP-11) | protocol |
| 10 | freeze the physical protocol | `physical-protocol.template.json` filled; registered with `harness freeze` (HWP-14) | registration |
| 11 | run the paired physical task instances in randomized blocks | analyzer report over retained trials (HWP-15, HWP-18) | report |
| 12 | blinded live-fault cell if the claim is kept | #348/#349 sealing followed (HWP-17) | sealed record |
| 13 | regenerate the analyzer report and sim-to-hardware deltas | `harness hardware report` from raw records (HWP-18) | report |
| 14 | update the claim matrix and public status | rows move off `hardware_pending` only with retained evidence (HWP-16, HWP-19) | `docs/claim-evidence.yaml` |

Roles on equipment day: physical safety operator (estop authority),
trial conductor (start/stop authority), software controller (torque
enable and evidence sink). One person may not hold all three.

# H1 composition experiment — results table (§8.2.4)

## Arm: claude

Treatment: commit `abd2e9d358f2`, model `claude-fable-5`.

| Metric | Value |
|---|---|
| Attempts | 20 |
| Zero-shot valid | 20 |
| Zero-shot valid AND launching | 3 |
| Zero-shot rate (H1 target >=0.80) | 0.15 |
| Working (<=3 cycles, pass@1 > 0) | 3 |
| Mean validate calls | 1.0 |
| Mean final pass@1 | 0.125 |
| Workspace violations | 0 |
| Sessions timed out | 0 |

| # | Perception choice | First-graph outcome | pass@1 | Cycles | Failures |
|---|---|---|---|---|---|
| 0 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 1 | detector-stack | no_episodes | 0.000 | 1 | - |
| 2 | detector-stack | no_episodes | 0.000 | 1 | - |
| 3 | detector-stack | no_episodes | 0.000 | 1 | - |
| 4 | detector-stack | no_episodes | 0.000 | 1 | - |
| 5 | detector-stack | no_episodes | 0.000 | 1 | - |
| 6 | detector-stack | no_episodes | 0.000 | 1 | - |
| 7 | detector-stack | no_episodes | 0.000 | 1 | - |
| 8 | detector-stack | no_episodes | 0.000 | 1 | - |
| 9 | detector-stack | no_episodes | 0.000 | 1 | - |
| 10 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 11 | detector-stack | no_episodes | 0.000 | 1 | - |
| 12 | detector-stack | no_episodes | 0.000 | 1 | - |
| 13 | detector-stack | no_episodes | 0.000 | 1 | - |
| 14 | detector-stack | no_episodes | 0.000 | 1 | - |
| 15 | oracle-pose | launched | 0.750 | 1 | dropped:1, collision:1 |
| 16 | detector-stack | no_episodes | 0.000 | 1 | - |
| 17 | detector-stack | no_episodes | 0.000 | 1 | - |
| 18 | detector-stack | no_episodes | 0.000 | 1 | - |
| 19 | detector-stack | no_episodes | 0.000 | 1 | - |

Mechanism split:

- `detector-stack`: 17 attempts, 0 launched
- `oracle-pose`: 3 attempts, 3 launched

## Arm: codex

Treatment: commit `abd2e9d358f2`, model `gpt-5.6-sol`.

| Metric | Value |
|---|---|
| Attempts | 20 |
| Zero-shot valid | 20 |
| Zero-shot valid AND launching | 13 |
| Zero-shot rate (H1 target >=0.80) | 0.65 |
| Working (<=3 cycles, pass@1 > 0) | 13 |
| Mean validate calls | 1.0 |
| Mean final pass@1 | 0.569 |
| Workspace violations | 0 |
| Sessions timed out | 0 |

| # | Perception choice | First-graph outcome | pass@1 | Cycles | Failures |
|---|---|---|---|---|---|
| 0 | detector-stack | no_episodes | 0.000 | 1 | - |
| 1 | detector-stack | no_episodes | 0.000 | 1 | - |
| 2 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 3 | detector-stack | no_episodes | 0.000 | 1 | - |
| 4 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 5 | detector-stack | no_episodes | 0.000 | 1 | - |
| 6 | oracle-pose | launched | 0.750 | 1 | dropped:1, collision:1 |
| 7 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 8 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 9 | detector-stack | no_episodes | 0.000 | 1 | - |
| 10 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 11 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 12 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 13 | oracle-pose | launched | 1.000 | 1 | - |
| 14 | detector-stack | no_episodes | 0.000 | 1 | - |
| 15 | detector-stack | no_episodes | 0.000 | 1 | - |
| 16 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 17 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 18 | oracle-pose | launched | 0.875 | 1 | collision:1 |
| 19 | oracle-pose | launched | 0.875 | 1 | collision:1 |

Mechanism split:

- `detector-stack`: 7 attempts, 0 launched
- `oracle-pose`: 13 attempts, 13 launched

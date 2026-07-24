# ADR-19 — Episode-independent store build; scenario resets by teleport (T16)

Status: accepted (CON-15: SPEC 200 defines the scenarios and generators
but not how S2/S3 episodes reach the physical scene; the agent picks and
records). Task: T16. Relates to [[ADR-15]] (store model), [[ADR-18]]
(S1 expert; supersedes its "S2/S3 need a rebuild per episode" known
limit).

## Decision

1. **The build set is episode-independent.** `build_store` spawns the
   FULL stock (`full_stock`: every slot item + one bin item per
   category, ADR-15) for every scenario and seed. The entity set never
   changes; only poses do.
2. **`episode_layout(plano, episode)` is the single pose source.** Pure
   in (planogram, episode) — CON-5: in-play items at their (possibly
   swapped, RS-9) spawn poses; S2's emptied-slot items on the STASH
   line (`STASH_Y` = -6.0, outside the store floor, spaced along x by
   stock index). Both the build AND the reset place items from it, so
   they cannot drift.
3. **`teleport_store_reset(handle, episode)` realizes any scenario.**
   State injection only (BRG-4/TC-6): teleport every item to the
   episode's layout. The bridge derives the episode from the reset
   SEED via the same `generate_episode(seed, scenario)` that produces
   the goal — the physical state and the task description share one
   generator and cannot disagree (RS-3).
4. **The verifier/detector roster is the full stock.** `build_retail_cfg`
   indexes oracle_state by `full_stock` order — an episode-shrunk
   roster would desync every index after an S2 slot. Stashed items sit
   far from every slot/counter, so they satisfy no placement rule.
5. **The store bridge accepts S1/S2/S3** (mobile-only and single-env
   still hold, ADR-13/ADR-18).

## Why not rebuild-per-episode

ADR-18 deferred S2/S3 assuming stock changes need entity add/remove.
They do not: "removed" stock is a pose (the stash), and S3 is a pose
swap `spawn_pose` already supports via `found_in`. Rebuilding per
episode would cost a ~2.5 min genesis build per seed and break the
one-scene TC-6 reset contract the whole harness is built on.

## Evidence

- tests/unit/test_store_cfg.py::TestEpisodeLayout (layout purity, stash,
  swap, determinism), ::test_retail_cfg_roster_is_full_stock_for_every_scenario
- tests/sim/test_store_scene.py::test_one_build_realizes_every_scenario_by_teleport
  — ONE built scene: S2 reset (slots vacant, bin stocked, winnable from
  the bin per RS-8), S3 reset (pair at found_in, winnable by re-shelving
  per RS-9), back to S1 (all spawn poses).

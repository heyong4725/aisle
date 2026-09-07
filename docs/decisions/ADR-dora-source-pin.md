# ADR-dora-source-pin — Support a source-pinned Dora CLI

Status: ACCEPTED — maintainer-authorized source installation; candidate adoption remains gated.

The maintainer authorized decoupling AISLE from Dora's release schedule (#517).
CON-3 already permits Cargo installation. AISLE may therefore select an exact
commit from the official Dora repository while separately pinning the Python
node API in pyproject.toml. dora-runtime.json binds the commit, Cargo.lock,
toolchain, profile and paired versions; tools/dora_runtime.py verifies those
inputs and retains an installation receipt with the resulting binary hash.
This records reproducible source selection and observed binary identity, not
bit-identical builds or a signed third-party attestation.

The current pin is a candidate with an unresolved upstream metadata-loss
finding. Installation is permitted for development, while quickstart
--runtime-prefix refuses benchmark execution until a reviewed pin update marks
the corrected source validated. That update requires upstream correction,
regression evidence and AISLE runtime validation; merging upstream or editing
the status field alone is not evidence. All existing independent, private
evaluation, frozen-environment and physical-evidence gates remain in force.

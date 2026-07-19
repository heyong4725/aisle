# ADR-3: sim-driver evalcards are pending until M0 (CAP-6 interpretation)

CAP-6 says eval may be null only while origin=hub and safety_class!=motion,
with the exception that "the two sim drivers ship with M0 evalcards generated
from TC-A1..A3 runs". At T02 those acceptance runs do not exist yet (bridge
lands at T05), so the evalcards cannot be honestly generated. Interpretation
chosen (CON-15): `arm-driver-sim` and `gripper-driver-sim` carry `eval: null`
and `registry.py lint` reports them as WARNINGS (not errors) naming the
pending TC-A1..A3 evalcards; fabricating placeholder evalcards was rejected
as dishonest data. When TC-A1..A3 first pass (T05/T10), the evalcards are
generated, the manifests updated, and the warning path in
`src/aisle/harness/registry.py` (PENDING_M0_EVALCARDS) removed — M0 review
should verify lint reports zero warnings. HARD GATE (T03 audit): the T10
acceptance suite (SPEC 090) MUST include a test asserting that registry lint
reports zero warnings and that PENDING_M0_EVALCARDS is empty; until then
`tests/unit/test_manifests.py::test_sim_driver_eval_exception_is_warning`
pins the warning set to exactly the two driver ids so the carve-out cannot
silently widen.

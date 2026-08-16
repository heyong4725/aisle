# Campaign skill sources — recovered, pending owner review

The #242 review recorded t2-scan-pose, t2-scan-tsm, and ik-transfer-v2
as "source not retained" (#245). That search covered branches, dangling
objects, and a checkout filesystem — but not the OPERATOR-side campaign
worktrees under the main repo's gitignored runs/, where the sources
were alive the whole time:

- runs/h3_desk/worktree_L/skills/{t2-scan-pose,t2-scan-tsm}
  (desk-H3 arm L, pin dd4e3f1a, registered during T2-r2)
- runs/a3/worktree_F/skills/ik-transfer-v2
  (A3 arm F, pin 8af9b47a)

Copied here VERBATIM (plus each registry manifest as manifest.yaml)
on 2026-08-16 by the dev loop, before any runs/ cleanup could make the
unreviewable verdict retroactively true. This directory is a REVIEW
STAGING AREA on a rescue branch — nothing here is registered or on the
curated path; the owner's #242 decisions for these three reopen:

- t2-scan-pose: review can now proceed (decision was "no fault found,
  nothing to merge" — there is now something to merge or decline).
- t2-scan-tsm: DECLINE was recorded on the evalcard alone (pass_rate
  0.0, below ADR-37's floor) — likely stands, but now verifiable.
- ik-transfer-v2: "the loss that stings" — the one motion-class skill.
  Recoverable; the open merge question is live again.

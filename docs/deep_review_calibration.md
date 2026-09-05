# Deep Judgement Review threshold calibration

Historical replay used the repository's persisted Story and Continuity data. A trigger was counted
only when evidence appeared after the latest Judgement record; counterevidence had to appear on the
replay day, and evidence accumulation fired only on the first threshold crossing. “Approximate
false positive” means a trigger on a day without a persisted WEAKENED/REVISED/OVERTURNED record;
it is conservative because historical no-change review decisions were not stored.

| Candidate | Triggered reviews | Days with review | Historical revision recall | Approx. false positives |
|---|---:|---:|---:|---:|
| 3 stories / 2 dates / 2 sources / 14d | 34 | 15 | 2/2 | 32 |
| 4 stories / 3 dates / 3 sources / 21d | 27 | 11 | 2/2 | 25 |
| 5 stories / 3 dates / 3 sources / 30d | 27 | 11 | 2/2 | 25 |
| 6 stories / 4 dates / 4 sources / 30d | 24 | 12 | 2/2 | 22 |

The provisional configuration is **4 stories / 3 dates / 3 sources / 21 days**. It retained both
historical real revisions, avoided a fixed daily sweep, and is less likely than the 3/2/2 option to
consume AI on ordinary overlap. The 5/3/3 and 6/4/4 variants did not materially reduce review days;
raising the threshold therefore provided little benefit in this small history. Dependency changes
remain mandatory candidates and do not require an AI call when no factual Story evidence exists.

This is a guardrail calibration, not a learned classifier. Revisit it after real trigger telemetry;
do not treat any threshold as permanent truth.

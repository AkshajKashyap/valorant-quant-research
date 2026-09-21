# Milestone 7: Prospective Failure-Mode Audit

> This is a post-hoc diagnostic of the already-observed frozen 100-match sample. It does not replace the Milestone 6 report, alter historical records, define a betting strategy, or constitute independent validation.

## Executive summary

The frozen sample and all 100 market/outcome orientations reproduce. No duplicate, winner-orientation, Bet365 home/away, odds-assignment, no-vig, timing-window, active-selection, or observed reschedule error was found. The frozen Elo underperformance is real as recorded: log loss 0.7998 versus 0.6998, and Brier 0.2856 versus 0.2505.

A confirmed identity-plumbing failure affected 125 of 200 competitor appearances and 88 of 100 forecasts. The pre-existing bridge had exact historical mappings for 37 teams, but every forecast used a `ps:<provider-id>` key. Those mapped appearances therefore received zero prior matches and rating 1500. A diagnostic identity-only repair, using only the mapping and match data available before the sample, changes 88 forecasts, moves Elo log loss to 0.7667, Brier to 0.2706, and mean absolute market disagreement from 21.49 pp to 14.49 pp. It still trails both the market and the neutral baseline, so identity failure explains part, not all, of v1's weakness.

A second confirmed limitation is stale state: all 100 forecasts say `state_through_date=2026-07-25`. Even using the prospective ledger alone as a conservative lower bound, 82 later forecasts had at least one prior-day result already observed before generation but absent from their state. The complete missing-history impact cannot be reconstructed from the ledger because it is not a complete PandaScore match feed.

## Data-integrity findings

- Frozen artifact equals an independent reconstruction of the first 100 strictly eligible ledger lifecycles: **True**.
- Unique fixture IDs: **100/100**; duplicates: **0**.
- Raw immutable odds hashes found and verified: **100/100**.
- Forecast/raw fixture order aligned home-to-team-A and away-to-team-B: **100/100**; reversed: **0**.
- Winner ID and `team_a_won` orientation agree: **100/100**.
- Bet365 ML decimal odds and no-vig probabilities reproduce: **100/100** and **100/100**.
- Forecasts predate effective starts, captures lie within 45–75 minutes, and primary choice reproduces: **100/100**, **100/100**, **100/100**.
- Rescheduled fixtures in the sample: **2** (1607787, 1608139). Their superseded primaries are excluded and their active replacement primaries are used.
- Non-identity integrity failures: **0**.

The collector nevertheless has a latent orientation hazard: fixture reconciliation is unordered while raw `home`/`away` prices are assigned directly to team A/B. It had zero impact here because all 100 pairs happened to share the same order; a future invariant must make that coincidence unnecessary.

## Cold-start breakdown

The registered classification uses only the counts frozen at forecast time. Because the identity audit proves many of those counts were wrong, "true" below means the protocol's stored-count definition, not the identity-reconciled finding.

| Category | Forecasts |
| --- | ---: |
| Both teams true cold starts as recorded | 41 |
| Exactly one cold-start team as recorded | 47 |
| Neither team a cold start as recorded | 12 |

All 41 exact-0.5 forecasts came from both stored ratings being the initial 1500; zero came from equal non-initial ratings. After applying only the already-existing exact identity mappings, 37 of those neutral forecasts had usable history for both teams and 4 had usable history for one. Thus every exact-0.5 forecast reflects confirmed missing continuity, not evidence inferred from the later winner.

Identity-reconciled pre-sample coverage is 96 both-history, 4 one-history, and 0 neither-history fixtures. This is an audit reclassification, not a rewrite of the registered cold-start fields.

## Team identity coverage

The 100 fixtures contain 61 unique PandaScore team IDs: 37 had an exact, unique historical-name mapping already recorded before forecasting; 22 were provider-only IDs with their own bridge history; and 2 first appeared after the bridge snapshot and had no pre-sample history.

| Coverage class | Teams | Checkpoint appearances | Finding |
| --- | ---: | ---: | --- |
| Exact historical mapping | 37 | 125 | Confirmed mapping existed but forecaster bypassed it |
| Provider-only with bridge history | 22 | 71 | Continuity within PandaScore bridge was retained |
| New after bridge snapshot | 2 | 4 | True pre-sample cold starts |

Exact mapped teams (PandaScore ID → historical ID): 100 Thieves (128605 → 120); All Gamers (128974 → 1119); BBL Esports (128577 → 397); Bilibili Gaming (133379 → 12010); Cloud9 (128819 → 188); DetonatioN FocusMe (131974 → 278); Dragon Ranger Gaming (133279 → 11981); EDward Gaming (128976 → 1120); Enterprise Esports (130973 → 876); Eternal Fire (129662 → 6392); Evil Geniuses (129181 → 5248); Fire Flux Esports (129194 → 4686); Fnatic (128537 → 2593); FULL SENSE (128912 → 4050); FUT Esports (128578 → 1184); Gentle Mates (133115 → 12694); GIANTX (134423 → 14419); Global Esports (129660 → 918); Karmine Corp (130922 → 8877); KRÜ Esports (128944 → 2355); LOUD (130338 → 6961); MIBR (130190 → 7386); Natus Vincere (129355 → 4915); Nova Esports (133287 → 12064); Paper Rex (128917 → 624); Rex Regum Qeon (130638 → 878); Sentinels (128472 → 2); Sharper Esport (129076 → 623); T1 (128647 → 14); Team Heretics (128622 → 1001); Team Liquid (128541 → 474); Team Secret (129537 → 6199); Team Vitality (128796 → 2059); Trace Esports (133380 → 12685); TYLOO (133288 → 731); Wolves Esports (134335 → 13790); ZETA DIVISION (129326 → 5448).

Provider-only teams with bridge history:  REBORN (138449, 20 matches); 2GAME Esports (134470, 55 matches); BESTIA (138619, 11 matches); Eintracht Frankfurt (134406, 73 matches); FunPlus Phoenix (128540, 63 matches); FURIA Esports (128477, 43 matches); G2 Esports (128538, 97 matches); Gen.G Esports (128473, 72 matches); JD Gaming (134454, 62 matches); Joblife (132476, 73 matches); Kiwoom DRX (130137, 88 matches); Leviatán Esports (128990, 56 matches); M80 (132233, 54 matches); Nongshim RedForce (132692, 68 matches); NRG (128471, 85 matches); ONSIDE GAMING (137113, 35 matches); Pcific Esports (134104, 21 matches); QT DIG∞ (136807, 25 matches); Team Envy (128470, 49 matches); TEC Esports (133825, 63 matches); VARREL (134426, 13 matches); Xipto Esports (135281, 32 matches).

True new teams: A Team (139027); Fluxo W7M (139091).

No additional historical alias or provider-ID-change mapping had independently verifiable pre-match evidence in the preserved inputs. Among checkpoint teams, no historical ID mapped to multiple PandaScore IDs, no normalized name appeared under multiple PandaScore IDs, and no checkpoint PandaScore ID used multiple names in the bridge. These checks found no duplicate organizational identity or preserved rename signal. Name-similar candidates were deliberately not merged. The machine-readable artifact contains the full inventory and appearance counts.

## Loss decomposition

Differences are Elo minus market. `gap sum` is the group's additive numerator; `contribution` divides that sum by all 100 matches, so contributions add to the aggregate gap. These are exhaustive post-hoc diagnostics.

### By recorded cold-start / history coverage

Recorded history coverage (both zero / exactly one zero / neither zero) is algebraically identical to the requested cold-start split, so it is shown once rather than duplicated under a second label.

| Group | n | Elo LL | Market LL | LL gap sum | LL contribution | Elo Brier | Market Brier | Brier gap sum | Brier contribution |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| both_cold_start | 41 | 0.6931 | 0.6894 | 0.1519 | 0.0015 | 0.2500 | 0.2458 | 0.1712 | 0.0017 |
| exactly_one_cold_start | 47 | 0.9355 | 0.7558 | 8.4483 | 0.0845 | 0.3328 | 0.2755 | 2.6945 | 0.0269 |
| neither_cold_start | 12 | 0.6328 | 0.5157 | 1.4043 | 0.0140 | 0.2225 | 0.1681 | 0.6527 | 0.0065 |

The one-cold group contributes most of the aggregate deficit: its log-loss contribution is 0.0845.

### By preregistered disagreement bucket

| Group | n | Elo LL | Market LL | LL gap sum | LL contribution | Elo Brier | Market Brier | Brier gap sum | Brier contribution |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| < 2 pp | 7 | 0.5841 | 0.5930 | -0.0624 | -0.0006 | 0.2022 | 0.2061 | -0.0273 | -0.0003 |
| 2–5 pp | 10 | 0.6464 | 0.6142 | 0.3220 | 0.0032 | 0.2269 | 0.2107 | 0.1620 | 0.0016 |
| 5–10 pp | 16 | 0.8357 | 0.8574 | -0.3479 | -0.0035 | 0.3070 | 0.3255 | -0.2951 | -0.0030 |
| > 10 pp | 67 | 0.8367 | 0.6860 | 10.0927 | 0.1009 | 0.2980 | 0.2431 | 3.6788 | 0.0368 |

### By UTC calendar date

| Group | n | Elo LL | Market LL | LL gap sum | LL contribution | Elo Brier | Market Brier | Brier gap sum | Brier contribution |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-08-05 | 1 | 0.6931 | 0.3117 | 0.3815 | 0.0038 | 0.2500 | 0.0717 | 0.1783 | 0.0018 |
| 2026-08-06 | 4 | 1.0076 | 0.7407 | 1.0674 | 0.0107 | 0.3680 | 0.2727 | 0.3814 | 0.0038 |
| 2026-08-07 | 7 | 0.4675 | 0.8254 | -2.5052 | -0.0251 | 0.1596 | 0.2882 | -0.9004 | -0.0090 |
| 2026-08-08 | 5 | 1.2335 | 0.7998 | 2.1687 | 0.0217 | 0.4527 | 0.2889 | 0.8189 | 0.0082 |
| 2026-08-09 | 8 | 0.7584 | 0.8252 | -0.5348 | -0.0053 | 0.2591 | 0.3133 | -0.4334 | -0.0043 |
| 2026-08-11 | 1 | 0.6931 | 0.9808 | -0.2877 | -0.0029 | 0.2500 | 0.3906 | -0.1406 | -0.0014 |
| 2026-08-13 | 5 | 0.7188 | 0.5622 | 0.7827 | 0.0078 | 0.2605 | 0.1870 | 0.3677 | 0.0037 |
| 2026-08-14 | 7 | 0.8451 | 0.8137 | 0.2197 | 0.0022 | 0.3202 | 0.3079 | 0.0859 | 0.0009 |
| 2026-08-15 | 6 | 0.6034 | 0.5382 | 0.3914 | 0.0039 | 0.2118 | 0.1754 | 0.2183 | 0.0022 |
| 2026-08-16 | 6 | 0.9569 | 0.7138 | 1.4587 | 0.0146 | 0.3495 | 0.2566 | 0.5571 | 0.0056 |
| 2026-08-17 | 1 | 0.6931 | 0.8176 | -0.1245 | -0.0012 | 0.2500 | 0.3119 | -0.0619 | -0.0006 |
| 2026-08-19 | 4 | 0.4987 | 0.9146 | -1.6637 | -0.0166 | 0.1648 | 0.3466 | -0.7274 | -0.0073 |
| 2026-08-20 | 7 | 0.9236 | 0.6153 | 2.1583 | 0.0216 | 0.3520 | 0.2142 | 0.9648 | 0.0096 |
| 2026-08-21 | 8 | 0.6256 | 0.6287 | -0.0246 | -0.0002 | 0.2275 | 0.2206 | 0.0558 | 0.0006 |
| 2026-08-22 | 7 | 0.7539 | 0.6578 | 0.6727 | 0.0067 | 0.2573 | 0.2323 | 0.1745 | 0.0017 |
| 2026-08-23 | 7 | 0.5438 | 0.6366 | -0.6497 | -0.0065 | 0.1819 | 0.2236 | -0.2920 | -0.0029 |
| 2026-08-24 | 1 | 0.6931 | 0.8176 | -0.1245 | -0.0012 | 0.2500 | 0.3119 | -0.0619 | -0.0006 |
| 2026-08-26 | 2 | 1.0107 | 0.7191 | 0.5833 | 0.0058 | 0.3698 | 0.2629 | 0.2138 | 0.0021 |
| 2026-08-27 | 3 | 0.8400 | 0.6210 | 0.6571 | 0.0066 | 0.3209 | 0.2141 | 0.3203 | 0.0032 |
| 2026-08-28 | 5 | 1.0010 | 0.4659 | 2.6752 | 0.0268 | 0.3758 | 0.1432 | 1.1629 | 0.0116 |
| 2026-08-29 | 5 | 1.3408 | 0.8004 | 2.7022 | 0.0270 | 0.4256 | 0.2983 | 0.6364 | 0.0064 |

## Neutral baseline verification

Over exactly the same outcomes, a constant 0.5 has log loss **0.6931** and Brier **0.2500**. Team A won 53/100, but that imbalance does not change the per-match 0.5 scores. Under the registered accuracy rule, every 0.5 is a no-direction tie, so neutral directional accuracy is undefined (0 directional predictions), not 50% observed accuracy.

Elo is worse than neutral by 0.1067 log loss and 0.0356 Brier. Bet365 is worse by 0.0066 and 0.0005, while still outperforming Elo. Market directional accuracy is 0.6042 (58/96); its slightly worse proper scores arise because probability confidence and error magnitude matter, and a small number of confident misses can outweigh more correct directions. With the orientation checks clean, this is not explained by a detected market-label error.

## Market disagreement diagnosis

The >10 pp bucket contains 67 matches and contributes 0.1009 of the 0.1000 aggregate log-loss gap. The identity-only reproduction reduces mean absolute disagreement by 7.01 pp, strong evidence that missing identity continuity drove much of the disagreement magnitude. Because repaired Elo still has 0.7667 log loss versus market 0.6998, the remaining gap is consistent with additional missing/current information and raw-Elo model limitations; this audit cannot assign those residual causes precisely.

## Confirmed versus suspected failure modes

Confirmed:

- Exact identity mappings were bypassed in 125 appearances across 88 matches.
- State was frozen through 2026-07-25 for all 100 forecasts; at least 82 forecasts omitted demonstrably available prior-day ledger results.
- Raw Elo lacks contextual strength information beyond the stale match-result stream; after the identity-only repair it still trails neutral and market scores.
- Fixture matching has a latent order-safety weakness, although no checkpoint row was reversed.

Suspected but not confirmed:

- Further cross-provider aliases, renamed organizations, or provider-ID changes may connect some provider-only teams to older historical IDs, but the preserved evidence does not justify a merge.
- Missing completed matches outside the prospective ledger likely make the stale-state lower bound incomplete; exact scope needs a separately preserved complete feed.
- Market inputs may encode roster, patch, tournament, or other current information absent from v1, but this audit does not isolate those channels.

Not supported:

- No observed fixture, winner, Bet365 home/away, odds, no-vig, duplicate, primary-selection, timing-window, or reschedule corruption explains the result.

## Limitations

The sample has only 100 outcomes and was already observed before this audit. Identity-repair scores are diagnostic counterfactuals, not a newly validated model. The static bridge prevents a complete reconstruction of all information that should have been available before each match. Organization continuity cannot be established from name similarity alone. No profitability, staking, or favorable-subset analysis was performed.

## Evidence-based requirements for a future v2

- Consume one versioned identity crosswalk consistently in both state construction and forecasting, and fail closed when the forecast key differs from the mapped state key.
- Preserve a complete, timestamped match feed and prove that every forecast state ends on D-1, with same-day matches batched under the registered rule.
- Distinguish true new teams from mapped teams and provider-only teams in frozen forecast records; store both provider and canonical IDs.
- Assert ordered team/price alignment against the raw market event before accepting a candidate.
- Reproduce ratings, counts, forecast probabilities, and market normalization from immutable inputs before a forecast can enter evaluation.
- Pre-register any added information source, feature, model family, hyperparameter, and missing-data behavior before collecting v2 outcomes.

## Proposed untouched prospective evaluation design for v2

Freeze the v2 specification only after the identity and state-freshness pipeline passes historical replay tests. Start a new append-only ledger namespace at a declared UTC activation time; no match whose outcome was known or included in this audit may enter v2 evaluation. Pre-register one primary model, one primary market comparator, eligibility and reschedule rules, the same proper scores, checkpoint size, and a date-clustered uncertainty method. Keep the current v1 collector and its records unchanged. Run v1 and v2 forecasts side by side only on newly scheduled fixtures, without updating specifications from interim outcomes, then evaluate the untouched chronological sample once the registered count is reached.

## Reproduction

Run `python -m valorant_quant.milestone7_audit`. The command reads immutable inputs and writes only `artifacts/milestone_7_failure_mode_audit.json` and this report. Source SHA-256 values are recorded in the artifact.

# Milestone 6 collector operations

Run one safe polling cycle from WSL:

```bash
scripts/run_milestone6_once.sh
```

Dry-run (no ledger or raw-snapshot write):

```bash
.venv/bin/python -m valorant_quant.milestone6_runner --dry-run
```

Non-evaluative status:

```bash
.venv/bin/python -m valorant_quant.milestone6_runner --status
```

For Windows Task Scheduler, create a task that runs every 15 minutes with
**Program/script** `C:\Windows\System32\wsl.exe` and **Arguments**:

```text
-d <YourWslDistribution> -- bash -lc '/home/akshaj/Building/valorant-quant-research/scripts/run_milestone6_once.sh'
```

The script activates the local virtual environment and sources the ignored
`.env`; it neither prints nor stores credentials. Keep "Start in" empty.

The runner first queries PandaScore upcoming fixtures. It does not query the
Odds API event hierarchy unless at least one fixture is currently in the
registered T-45/T-75 window; it then requests event odds only for reconciled
window candidates. Static league discovery is currently repeated only in that
rare window path, not on every no-candidate poll.

This command is not yet enabled as a Windows task. Enable it only after the
remaining forecast, reschedule, and outcome append paths have been reviewed.

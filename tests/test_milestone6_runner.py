from datetime import datetime, timezone

from valorant_quant import milestone6_runner as runner


def test_dry_run_is_idempotent_and_does_not_append(monkeypatch, tmp_path):
    monkeypatch.setattr(runner,"ROOT",tmp_path); monkeypatch.setattr(runner,"LEDGER",tmp_path/"ledger.jsonl")
    monkeypatch.setattr(runner,"load_credentials",lambda:("p","o"))
    fixture={"id":1,"scheduled_at":"2026-07-26T13:00:00Z","opponents":[{"opponent":{"name":"A"}},{"opponent":{"name":"B"}}]}
    event={"id":2,"home":"A","away":"B","date":"2026-07-26T13:00:00Z"}
    odds={"bookmakers":{"Bet365":[{"name":"ML","updatedAt":"x","odds":[{"home":"2.0","away":"2.0"}]}]}}
    def fake(url, **kwargs):
        if "upcoming" in url:return [fixture]
        if "leagues" in url:return [{"name":"Valorant","slug":"v"}]
        if "events" in url:return [event]
        return odds
    monkeypatch.setattr(runner,"get_json",fake)
    result=runner.run_once(dry_run=True,now=datetime(2026,7,26,12,tzinfo=timezone.utc))
    assert result["would_append"]==1 and not runner.LEDGER.exists()


def test_status_has_no_performance_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(runner,"LEDGER",tmp_path/"missing")
    assert set(runner.status())=={"target","forecasts_frozen","primary_snapshots","outcomes_attached","candidate_snapshots"}

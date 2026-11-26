from connect_sim.logs import prune
from helpers import dump


def make_runs(base, scenario, stamps):
    for stamp in stamps:
        dump(base / f"{scenario}-{stamp}" / "summary.json", {"verdict": "PASSOU"})


def test_prune_keeps_the_newest_runs_of_one_scenario(tmp_path):
    base = tmp_path / "connect-sim" / "flow"
    make_runs(base, "ok", ["20260101T000001Z", "20260101T000002Z", "20260101T000003Z"])
    removed = prune(tmp_path, "flow", "ok", keep=2)
    assert [p.name for p in removed] == ["ok-20260101T000001Z"]
    assert sorted(p.name for p in base.iterdir()) == ["ok-20260101T000002Z", "ok-20260101T000003Z"]


def test_prune_never_touches_a_scenario_with_a_longer_name(tmp_path):
    base = tmp_path / "connect-sim" / "flow"
    make_runs(base, "ok", ["20260101T000001Z", "20260101T000002Z"])
    make_runs(base, "ok-2", ["20260101T000001Z"])
    prune(tmp_path, "flow", "ok", keep=1)
    assert (base / "ok-2-20260101T000001Z").is_dir()


def test_prune_without_a_limit_removes_nothing(tmp_path):
    base = tmp_path / "connect-sim" / "flow"
    make_runs(base, "ok", ["20260101T000001Z", "20260101T000002Z"])
    assert prune(tmp_path, "flow", "ok", keep=None) == []
    assert len(list(base.iterdir())) == 2

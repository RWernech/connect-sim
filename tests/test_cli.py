import json

from connect_sim.cli import main
from helpers import dump, end_action, flow_with


def simple_flow(cliente):
    """Passes check, writes an attribute and ends. Creates no case."""
    actions = [
        {"Identifier": "A", "Type": "UpdateContactAttributes",
         "Parameters": {"Attributes": {"Canal": "chat"}, "TargetContact": "Current"},
         "Transitions": {"NextAction": "Fim"}},
        end_action(),
    ]
    return dump(cliente.root / "flows" / "simples.json", flow_with(actions, "A"))


def run_all(cliente, scenarios_dir, flow, tmp_path):
    return main([
        "run-all", "--flow", str(flow), "--config", str(cliente.config),
        "--scenarios", str(scenarios_dir), "--runs-dir", str(tmp_path / "runs"),
    ])


def scenario(folder, name, **extra):
    data = {"name": name, "attributes": {}, "expect": {"outcome": "end_flow"}}
    data.update(extra)
    return dump(folder / f"{name}.json", data)


def test_passing_scenario_exits_zero(cliente, tmp_path):
    flow = simple_flow(cliente)
    folder = tmp_path / "cenarios"
    scenario(folder, "ok")
    assert run_all(cliente, folder, flow, tmp_path) == 0


def test_unknown_view_action_stops_the_scenario(cliente, tmp_path):
    flow = simple_flow(cliente)
    folder = tmp_path / "cenarios"
    scenario(folder, "acao-errada", views={"hub": [{"action": "Nao existe", "data": {}}]})
    assert run_all(cliente, folder, flow, tmp_path) == 1


def test_expected_divergence_that_reproduces_passes(cliente, tmp_path):
    # The scenario expects one case, but the flow creates none: the divergence reproduces.
    flow = simple_flow(cliente)
    folder = tmp_path / "cenarios"
    scenario(folder, "diverge", expect_divergence=True, expect={"outcome": "end_flow", "cases": 1})
    assert run_all(cliente, folder, flow, tmp_path) == 0


def test_expected_divergence_that_does_not_reproduce_fails(cliente, tmp_path):
    # The expectation matches the run, so the known problem no longer shows.
    flow = simple_flow(cliente)
    folder = tmp_path / "cenarios"
    scenario(folder, "sumiu", expect_divergence=True, expect={"outcome": "end_flow"})
    assert run_all(cliente, folder, flow, tmp_path) == 1


def test_expected_divergence_without_expect_fails(cliente, tmp_path):
    flow = simple_flow(cliente)
    folder = tmp_path / "cenarios"
    dump(folder / "sem-expect.json", {"name": "sem-expect", "expect_divergence": True})
    assert run_all(cliente, folder, flow, tmp_path) == 1


def test_conferir_separates_new_from_accepted(cliente, tmp_path):
    # Both views and modules are missing from the export, so conferir reports failures.
    # Accepting them turns the exit code back to zero.
    from connect_sim.conferir import gravar_aceitas
    from connect_sim.config import Config

    raw = dump(cliente.root / "config" / "aceitas.json", {"aceitas": []})
    config_raw = json.loads(cliente.config.read_text(encoding="utf-8"))
    config_raw["divergencias_aceitas"] = raw.name
    cliente.config.write_text(json.dumps(config_raw), encoding="utf-8")

    assert main(["conferir", "--config", str(cliente.config)]) == 1
    gravar_aceitas(Config(cliente.config), ["view hub: sem export; rode connect-sim baixar com um flow que a use",
                                            "modulo modulo: sem export; rode connect-sim baixar com um flow que o use"])
    assert main(["conferir", "--config", str(cliente.config)]) == 0

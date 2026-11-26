from connect_sim.config import Config
from connect_sim.engine import Engine
from connect_sim.flow import Flow
from connect_sim.scenario import Scenario
from helpers import dump, end_action, flow_with


def run_flow(cliente, actions, start="A", views=None, attributes=None):
    path = dump(cliente.root / "flows" / "f.json", flow_with(actions, start))
    events = []
    engine = Engine(
        Flow(path), Config(cliente.config),
        Scenario(name="t", views=views or {}, attributes=attributes or {}),
        events.append,
    )
    return engine.run(), events


def test_show_view_routes_by_placeholder_condition(cliente):
    result, _ = run_flow(
        cliente,
        [
            {"Identifier": "A", "Type": "ShowView",
             "Parameters": {"ViewResource": {"Id": "{{view.hub.id}}", "Version": "latest"}, "ViewData": {}},
             "Transitions": {
                 "Conditions": [{"Condition": {"Operator": "Equals", "Operands": ["{{view.hub.acao_salvar}}"]},
                                 "NextAction": "Fim"}],
                 "Errors": [{"ErrorType": "NoMatchingCondition", "NextAction": "Fim"}]}},
            end_action(),
        ],
        views={"hub": [{"action": "Salvar Tabulacao", "data": {}}]},
    )
    assert result.outcome == "end_flow"
    assert result.path == ["A", "Fim"]


def test_optional_attribute_stays_silent_when_empty(cliente):
    _, events = run_flow(cliente, [
        {"Identifier": "A", "Type": "UpdateContactAttributes",
         "Parameters": {"Attributes": {"TIObs": "$.Attributes.TIObs"}, "TargetContact": "Current"},
         "Transitions": {"NextAction": "Fim"}},
        end_action(),
    ])
    assert events[0]["notes"] == []


def test_required_attribute_warns_when_empty(cliente):
    _, events = run_flow(cliente, [
        {"Identifier": "A", "Type": "UpdateContactAttributes",
         "Parameters": {"Attributes": {"Outro": "$.Attributes.Outro"}, "TargetContact": "Current"},
         "Transitions": {"NextAction": "Fim"}},
        end_action(),
    ])
    assert any("esta vazio" in note for note in events[0]["notes"])


def test_several_references_in_one_value_are_joined(cliente):
    path = dump(cliente.root / "flows" / "f.json", flow_with([end_action()], "Fim"))
    engine = Engine(Flow(path), Config(cliente.config), Scenario(name="t"), lambda _e: None)
    engine.external = {"firstName": "Ana", "lastName": "Lima"}
    reads = []
    value = engine._dyn("$.External.firstName $.External.lastName", reads)
    assert value == "Ana Lima"
    assert any("junta 2 caminhos" in note for note in reads)

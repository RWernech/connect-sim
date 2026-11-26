from connect_sim.config import Config
from connect_sim.flow import Flow, check
from helpers import compare_module, dump, end_action, flow_with


def run_check(cliente, actions, start="A"):
    path = dump(cliente.root / "flows" / "f.json", flow_with(actions, start))
    return check(Flow(path), Config(cliente.config))


def messages(report, level):
    return [f.message for f in report.findings if f.level == level]


def test_clean_flow_has_no_errors(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "UpdateContactAttributes",
         "Parameters": {"Attributes": {"Canal": "chat"}, "TargetContact": "Current"},
         "Transitions": {"NextAction": "Fim"}},
        end_action(),
    ])
    assert report.errors == []


def test_unknown_action_type_is_an_error(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "NaoExiste", "Parameters": {}, "Transitions": {"NextAction": "Fim"}},
        end_action(),
    ])
    assert any("NaoExiste" in m for m in messages(report, "ERROR"))


def test_dangling_next_action_is_an_error(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "UpdateContactAttributes",
         "Parameters": {"Attributes": {}, "TargetContact": "Current"},
         "Transitions": {"NextAction": "Sumiu"}},
        end_action(),
    ])
    assert any("Sumiu" in m for m in messages(report, "ERROR"))


def test_show_view_condition_must_be_an_action_the_view_answers(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "ShowView",
         "Parameters": {"ViewResource": {"Id": "{{view.hub.id}}", "Version": "latest"}, "ViewData": {}},
         "Transitions": {
             "Conditions": [{"Condition": {"Operator": "Equals", "Operands": ["Salvar Errado"]}, "NextAction": "Fim"}],
             "Errors": [{"ErrorType": "NoMatchingCondition", "NextAction": "Fim"}]}},
        end_action(),
    ])
    assert any("Salvar Errado" in m and "view 'hub'" in m for m in messages(report, "ERROR"))


def test_show_view_placeholder_action_is_accepted(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "ShowView",
         "Parameters": {"ViewResource": {"Id": "{{view.hub.id}}", "Version": "latest"}, "ViewData": {}},
         "Transitions": {
             "Conditions": [{"Condition": {"Operator": "Equals", "Operands": ["{{view.hub.acao_salvar}}"]}, "NextAction": "Fim"}],
             "Errors": [{"ErrorType": "NoMatchingCondition", "NextAction": "Fim"}]}},
        end_action(),
    ])
    assert not [m for m in messages(report, "ERROR") if "view 'hub'" in m]


def test_compare_option_with_only_accent_difference_gets_a_hint(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "Compare", "Parameters": {"ComparisonValue": "$.Attributes.TIMotivo"},
         "Transitions": {
             "Conditions": [{"Condition": {"Operator": "Equals", "Operands": ["Erro Ok"]}, "NextAction": "Fim"}],
             "Errors": [{"ErrorType": "NoMatchingCondition", "NextAction": "Fim"}]}},
        end_action(),
    ])
    errors = messages(report, "ERROR")
    assert any("'Erro Ok'" in m and "so acento ou caixa" in m for m in errors)


def test_compare_inactive_option_is_a_warning(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "Compare", "Parameters": {"ComparisonValue": "$.Attributes.TIMotivo"},
         "Transitions": {
             "Conditions": [{"Condition": {"Operator": "Equals", "Operands": ["Antiga"]}, "NextAction": "Fim"}],
             "Errors": [{"ErrorType": "NoMatchingCondition", "NextAction": "Fim"}]}},
        end_action(),
    ])
    assert any("INATIVA" in m for m in messages(report, "WARN"))
    assert not report.errors


def test_called_module_is_checked_too(cliente):
    # The module compares against a value that is not an active option.
    dump(cliente.modulo, compare_module(operand="Nao existe"))
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "InvokeFlowModule",
         "Parameters": {"FlowModuleId": "{{module.modulo}}"},
         "Transitions": {"NextAction": "Fim", "Errors": [{"ErrorType": "NoMatchingError", "NextAction": "Fim"}]}},
        end_action(),
    ])
    errors = [f for f in report.findings if f.level == "ERROR"]
    assert any(f.where.startswith("modulo:") and "Nao existe" in f.message for f in errors)


def test_malformed_placeholder_is_an_error(cliente):
    report = run_check(cliente, [
        {"Identifier": "A", "Type": "UpdateContactAttributes",
         "Parameters": {"Attributes": {"x": "{{view.hub"}, "TargetContact": "Current"},
         "Transitions": {"NextAction": "Fim"}},
        end_action(),
    ])
    assert any("mal formado" in m for m in messages(report, "ERROR"))

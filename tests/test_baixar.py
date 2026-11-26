import json

import pytest

from connect_sim.baixar import export_dir, plan, run
from connect_sim.config import Config, ConfigError
from connect_sim.flow import Flow
from helpers import dump, end_action, flow_with


def module_that_creates_case(cliente):
    """Rewrite the module so it creates the template 'caso' by its literal ID."""
    module = {
        "Version": "2019-10-30",
        "StartAction": "Caso",
        "Actions": [
            {"Identifier": "Caso", "Type": "CreateCase",
             "Parameters": {"CaseTemplateId": "t1", "CaseRequestFields": {}},
             "Transitions": {"NextAction": "Fim", "Errors": [{"ErrorType": "NoMatchingError", "NextAction": "Fim"}]}},
            {"Identifier": "Fim", "Type": "EndFlowModuleExecution", "Parameters": {}, "Transitions": {}},
        ],
    }
    dump(cliente.modulo, module)


def flow_using_view_and_module(cliente):
    actions = [
        {"Identifier": "A", "Type": "ShowView",
         "Parameters": {"ViewResource": {"Id": "{{view.hub.id}}", "Version": "latest"}, "ViewData": {}},
         "Transitions": {"NextAction": "B", "Errors": [{"ErrorType": "NoMatchingError", "NextAction": "Fim"}]}},
        {"Identifier": "B", "Type": "InvokeFlowModule",
         "Parameters": {"FlowModuleId": "{{module.modulo}}"},
         "Transitions": {"NextAction": "Fim", "Errors": [{"ErrorType": "NoMatchingError", "NextAction": "Fim"}]}},
        end_action(),
    ]
    return Flow(dump(cliente.root / "flows" / "f.json", flow_with(actions, "A")))


def test_export_folder_outside_the_client_is_refused(tmp_path):
    config_path = dump(tmp_path / "solto" / "config" / "sim.json", {
        "exportacao": "../00-inputs", "views": {}, "modules": {}, "templates": {}, "lambdas": {},
    })
    with pytest.raises(ConfigError, match="fora de work"):
        export_dir(Config(config_path))


def test_export_folder_pointing_outside_the_client_is_refused(cliente):
    raw = json.loads(cliente.config.read_text(encoding="utf-8"))
    raw["exportacao"] = "../../../../fora"
    cliente.config.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="aponta para fora"):
        export_dir(Config(cliente.config))


def test_export_folder_inside_the_client_is_accepted(cliente):
    assert export_dir(Config(cliente.config)) == cliente.export.resolve()


def test_plan_lists_views_modules_templates_and_catalogs(cliente):
    module_that_creates_case(cliente)
    config = Config(cliente.config)
    items = plan(config, [flow_using_view_and_module(cliente)])
    assert ("view", "hub") in items
    assert ("module", "modulo") in items
    assert ("template", "caso") in items
    assert ("opcoes", "motivo") in items


def test_dry_run_lists_without_calling_aws_or_writing(cliente):
    module_that_creates_case(cliente)
    before = sorted(p.name for p in cliente.export.iterdir())
    env = {"environment": "stg", "profile": "nao-usar", "region": "us-east-1", "instance": "x", "domain": "y"}
    code = run(Config(cliente.config), env, [flow_using_view_and_module(cliente)], dry_run=True)
    assert code == 0
    assert sorted(p.name for p in cliente.export.iterdir()) == before


def test_environment_must_match_the_config(cliente):
    env = {"environment": "qa", "profile": "x", "region": "us-east-1", "instance": "x", "domain": "y"}
    with pytest.raises(ConfigError, match="difere do config"):
        run(Config(cliente.config), env, [flow_using_view_and_module(cliente)], dry_run=True)

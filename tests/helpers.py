"""Builders for the test data. Plain functions, imported by the test modules."""

import json


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare_module(operand="Erro ok"):
    """A module that compares TIMotivo with one operand and ends."""
    return {
        "Version": "2019-10-30",
        "StartAction": "TC Motivo",
        "Actions": [
            {
                "Identifier": "TC Motivo",
                "Type": "Compare",
                "Parameters": {"ComparisonValue": "$.Attributes.TIMotivo"},
                "Transitions": {
                    "Conditions": [{"Condition": {"Operator": "Equals", "Operands": [operand]}, "NextAction": "Fim"}],
                    "Errors": [{"ErrorType": "NoMatchingCondition", "NextAction": "Fim"}],
                },
            },
            {"Identifier": "Fim", "Type": "EndFlowModuleExecution", "Parameters": {}, "Transitions": {}},
        ],
    }


def flow_with(actions, start):
    return {"Version": "2019-10-30", "StartAction": start, "Actions": actions}


def end_action(identifier="Fim"):
    return {"Identifier": identifier, "Type": "EndFlowExecution", "Parameters": {}, "Transitions": {}}

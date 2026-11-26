"""Scenario files: what a simulated contact does, and what the run must produce.

A scenario supplies the initial contact attributes, the customer's answers, the
agent's view answers, queue state, Lambda and profile overrides, and the expected
result. The expectations are checked after the run.

A scenario can mark a known divergence with "expect_divergence": true. Such a
scenario passes when the run differs from its expect block, because the difference
is the behavior under test. It fails when the run matches the expectation, since
then the known problem no longer reproduces.

View actions may use placeholders, such as {{view.triagem.acao_enviar}}.
resolve_views replaces them with the values in the config and rejects any action
that the view does not answer.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Scenario:
    name: str
    description: str = ""
    contact: dict = field(default_factory=dict)
    attributes: dict = field(default_factory=dict)
    inputs: list = field(default_factory=list)
    views: dict = field(default_factory=dict)
    queues: dict = field(default_factory=dict)
    lambdas: dict = field(default_factory=dict)
    profile: str = "found"
    case_link_fails: bool = False
    expect: dict = field(default_factory=dict)
    expect_divergence: bool = False


def load_scenario(path):
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return Scenario(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        contact=raw.get("contact", {}),
        attributes=raw.get("attributes", {}),
        inputs=list(raw.get("inputs", [])),
        views=raw.get("views", {}),
        queues=raw.get("queues", {}),
        lambdas=raw.get("lambdas", {}),
        profile=raw.get("profile", "found"),
        case_link_fails=bool(raw.get("case_link_fails", False)),
        expect=raw.get("expect", {}),
        expect_divergence=bool(raw.get("expect_divergence", False)),
    )


def resolve_views(scenario, config):
    """Replace placeholders in the view actions and check each action against the view.

    Return a list of problems. The scenario's views are replaced only when no
    problem was found, so a broken scenario never runs with a half-resolved name.
    """
    problems = []
    resolved = {}
    for view_name, items in scenario.views.items():
        allowed = config.view_actions(view_name)
        if allowed is None:
            problems.append(f"view '{view_name}' do cenario nao esta no config")
            continue
        new_items = []
        for item in items:
            missing = []
            action = config.resolve(str(item.get("action", "")), missing)
            problems.extend(missing)
            if action not in allowed:
                listed = ", ".join(sorted(allowed)) or "nenhuma acao configurada"
                problems.append(f"view '{view_name}': acao {action!r} nao existe; a view responde: {listed}")
            new_items.append({**item, "action": action})
        resolved[view_name] = new_items
    if not problems:
        scenario.views = resolved
    return problems


def mismatches(result, expect):
    """Compare a RunResult with the expect block. Return a list of divergences."""
    problems = []
    if "outcome" in expect and result.outcome != expect["outcome"]:
        problems.append(f"outcome esperado {expect['outcome']!r}, obtido {result.outcome!r}")
    if "path" in expect and result.path != expect["path"]:
        problems.append(f"caminho esperado {expect['path']}, obtido {result.path}")
    if "failed_at" in expect and result.failed_at != expect["failed_at"]:
        problems.append(f"falhou em {expect['failed_at']!r}, obtido {result.failed_at!r}")
    if "cases" in expect and len(result.cases) != expect["cases"]:
        problems.append(f"casos criados esperado {expect['cases']}, obtido {len(result.cases)}")
    for key, value in expect.get("attributes", {}).items():
        got = result.attributes.get(key)
        if got != value:
            problems.append(f"atributo {key}: esperado {value!r}, obtido {got!r}")
    return problems

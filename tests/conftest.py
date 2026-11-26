"""Shared fixtures. Every test builds its own client folder under tmp_path.

The layout matches a real client (work/<cliente>/config, 00-inputs/aws-export/<env>),
but the IDs and names are invented, so the tests never touch client data.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from helpers import compare_module, dump


@dataclass
class Cliente:
    root: Path
    config: Path
    export: Path
    modulo: Path


@pytest.fixture
def cliente(tmp_path):
    """A client with one view, one template, one module, one catalog and an export folder."""
    root = tmp_path / "work" / "cliente"
    config = dump(root / "config" / "sim.json", {
        "ambiente": "stg",
        "exportacao": "../00-inputs/aws-export/stg",
        "views": {"hub": {"id": "v1", "version": "latest", "acao_salvar": "Salvar Tabulacao"}},
        "templates": {"caso": {"id": "t1", "fields": {"motivo": {"id": "f1"}}}},
        "lambdas": {},
        "modules": {"modulo": {"id": "m1", "file": "../modulos/modulo.json"}},
        "opcoes": {"motivo": {"atributo": "TIMotivo", "campo_id": "f1", "view": "hub"}},
        "agente_teste": {"usuario": "", "nome": "Agente", "sobrenome": "Teste"},
        "atributos_opcionais": ["TIObs"],
    })
    modulo = dump(root / "modulos" / "modulo.json", compare_module())
    export = root / "00-inputs" / "aws-export" / "stg"
    dump(export / "opcoes-motivo.json", [
        {"value": "Erro ok", "name": "Erro ok"},
        {"value": "Antiga", "name": "Antiga", "status": "INACTIVE"},
    ])
    return Cliente(root=root, config=config, export=export, modulo=modulo)

import json

import pytest

from connect_sim.config import Config, ConfigError
from helpers import dump


def test_placeholder_resolves_to_config_value(cliente):
    config = Config(cliente.config)
    missing = []
    assert config.resolve("{{view.hub.acao_salvar}}", missing) == "Salvar Tabulacao"
    assert missing == []


def test_unknown_placeholder_is_reported(cliente):
    config = Config(cliente.config)
    missing = []
    config.resolve("{{view.naoexiste.acao_salvar}}", missing)
    assert missing and "naoexiste" in missing[0]


def test_view_actions_read_acao_keys_and_actions_list(cliente):
    config = Config(cliente.config)
    assert config.view_actions("hub") == {"Salvar Tabulacao"}
    assert config.view_actions("nao-existe") is None


def test_option_values_split_active_and_inactive(cliente):
    config = Config(cliente.config)
    attribute, active, inactive = config.option_values("motivo")
    assert attribute == "TIMotivo"
    assert active == {"Erro ok"}
    assert inactive == {"Antiga"}


def test_option_values_fail_when_export_is_missing(cliente):
    (cliente.export / "opcoes-motivo.json").unlink()
    config = Config(cliente.config)
    with pytest.raises(ConfigError, match="nao baixadas"):
        config.option_values("motivo")


def test_export_path_needs_exportacao(tmp_path):
    path = dump(tmp_path / "work" / "c" / "config" / "sim.json", {"ambiente": "stg"})
    with pytest.raises(ConfigError, match="exportacao"):
        Config(path).export_path("x.json")


def test_optional_attribute_is_listed(cliente):
    config = Config(cliente.config)
    assert "TIObs" in config.opcionais


def test_divergencias_aceitas_empty_without_file(cliente):
    assert Config(cliente.config).divergencias_aceitas() == set()


def test_divergencias_aceitas_reads_keys(cliente):
    raw = json.loads(cliente.config.read_text(encoding="utf-8"))
    raw["divergencias_aceitas"] = "aceitas.json"
    cliente.config.write_text(json.dumps(raw), encoding="utf-8")
    dump(cliente.config.parent / "aceitas.json", {"aceitas": [{"chave": "SEM PAR 'X'", "motivo": "teste"}]})
    assert Config(cliente.config).divergencias_aceitas() == {"SEM PAR 'X'"}


def test_reference_reads_the_environment_file(tmp_path):
    env = dump(tmp_path / "work" / "c" / "config" / "ambiente.json", {"resources": {"views": {"hub": "v-123"}}})
    path = dump(tmp_path / "work" / "c" / "config" / "sim.json", {
        "ambiente_arquivo": "ambiente.json",
        "views": {"hub": {"id": "@resources.views.hub"}},
    })
    assert env.is_file()
    assert Config(path).views["hub"]["id"] == "v-123"


def test_missing_reference_is_a_config_error(tmp_path):
    dump(tmp_path / "work" / "c" / "config" / "ambiente.json", {"resources": {}})
    path = dump(tmp_path / "work" / "c" / "config" / "sim.json", {
        "ambiente_arquivo": "ambiente.json",
        "views": {"hub": {"id": "@resources.views.nao_existe"}},
    })
    with pytest.raises(ConfigError, match="nao existe"):
        Config(path)

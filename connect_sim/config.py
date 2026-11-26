"""Client config: maps the logical names used in flows to real IDs and local stand-ins.

A flow names its resources with placeholders such as {{queue.atendimento.id}}.
The config file gives each placeholder its value. One generic flow serves every
client, and each client gets its own config file.
"""

import json
import re
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{\s*([a-z]+)\.([A-Za-z0-9_\-]+)(?:\.([A-Za-z0-9_\-]+))?\s*\}\}")
DEFAULT_KEY = {"queue": "id", "view": "id", "template": "id", "lambda": "arn", "module": "id"}
TABLES = {"queue": "queues", "view": "views", "template": "templates", "lambda": "lambdas", "module": "modules"}


class ConfigError(Exception):
    """A placeholder names a resource that the config does not define."""


class Config:
    """Loaded client config, with lookups by placeholder and by real value."""

    def __init__(self, path):
        self.path = Path(path)
        self.base_dir = self.path.parent
        with open(self.path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if raw.get("ambiente_arquivo"):
            env_path = self.base_dir / raw["ambiente_arquivo"]
            if not env_path.is_file():
                raise ConfigError(f"ambiente_arquivo nao existe: {env_path}")
            env = json.loads(env_path.read_text(encoding="utf-8"))
            raw = resolve_refs(raw, env, env_path)
        self.queues = raw.get("queues", {})
        self.views = raw.get("views", {})
        self.templates = raw.get("templates", {})
        self.lambdas = raw.get("lambdas", {})
        self.modules = raw.get("modules", {})
        self.opcoes = raw.get("opcoes", {})
        self.exportacao = raw.get("exportacao")
        self.ambiente = raw.get("ambiente")
        self.agente = raw.get("agente_teste", {})
        self.opcionais = frozenset(raw.get("atributos_opcionais", []))
        self.divergencias_arquivo = raw.get("divergencias_aceitas")
        self.reverse = {}
        self._index()

    def _index(self):
        for name, entry in self.queues.items():
            self._put(entry.get("id"), ("queue", name, "id"))
        for name, entry in self.views.items():
            self._put(entry.get("id"), ("view", name, "id"))
        for name, tmpl in self.templates.items():
            self._put(tmpl.get("id"), ("template", name, "id"))
            for fname, fdef in tmpl.get("fields", {}).items():
                self._put(fdef.get("id"), ("field", name, fname))
        for name, entry in self.lambdas.items():
            self._put(entry.get("arn"), ("lambda", name, "arn"))
        for name, entry in self.modules.items():
            self._put(entry.get("id"), ("module", name, "id"))

    def _put(self, value, ref):
        if value:
            self.reverse[str(value)] = ref

    def ref_for(self, value):
        """Return (kind, name, sub) for a real ID or ARN, or None."""
        return self.reverse.get(str(value))

    def value(self, kind, name, sub=None):
        """Return the value behind one placeholder, or raise ConfigError."""
        if kind == "field":
            tmpl = self.templates.get(name)
            if tmpl is None:
                raise ConfigError(f"template '{name}' nao esta no config")
            entry = tmpl.get("fields", {}).get(sub)
            if entry is None:
                raise ConfigError(f"campo '{sub}' nao existe no template '{name}'")
            key = "id"
        elif kind in TABLES:
            entry = getattr(self, TABLES[kind]).get(name)
            if entry is None:
                raise ConfigError(f"{kind} '{name}' nao esta no config")
            key = sub or DEFAULT_KEY[kind]
        else:
            raise ConfigError(f"tipo de placeholder desconhecido: '{kind}'")
        if key not in entry:
            raise ConfigError(f"{kind}.{name} nao tem a chave '{key}'")
        return entry[key]

    def divergencias_aceitas(self):
        """Return the set of divergence keys already reviewed and accepted (empty when no file is set)."""
        if not self.divergencias_arquivo:
            return set()
        path = self.base_dir / self.divergencias_arquivo
        if not path.is_file():
            return set()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {item["chave"] for item in raw.get("aceitas", [])}

    def export_path(self, filename):
        """Return the path of one downloaded file in the config's export folder ('exportacao')."""
        if not self.exportacao:
            raise ConfigError("config sem 'exportacao': diga em qual pasta do cliente ficam os downloads")
        return (self.base_dir / self.exportacao / filename).resolve()

    def view_actions(self, name):
        """Return the action names a view answers, read from its acao_* keys, or None if the view is unknown."""
        entry = self.views.get(name)
        if entry is None:
            return None
        listed = [str(value) for value in entry.get("actions", [])]
        named = [str(value) for key, value in entry.items() if key.startswith("acao_")]
        return set(listed + named)

    def option_values(self, key):
        """Return (attribute, active values, inactive values) for an options catalog named in 'opcoes'.

        An option without a status field counts as active. The catalog file is read
        from the local export; no AWS call happens here. Raises ConfigError when the
        catalog or its file is missing or empty.
        """
        spec = self.opcoes.get(key)
        if spec is None:
            raise ConfigError(f"opcoes '{key}' nao esta no config")
        path = self.export_path(f"opcoes-{key}.json")
        if not path.is_file():
            raise ConfigError(f"opcoes '{key}' nao baixadas: falta {path} (rode connect-sim baixar)")
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not raw:
            raise ConfigError(f"arquivo de opcoes '{key}' esta vazio: {path}")
        active, inactive = set(), set()
        for opt in raw:
            value = str(opt.get("value"))
            if str(opt.get("status", "ACTIVE")).upper() == "INACTIVE":
                inactive.add(value)
            else:
                active.add(value)
        return spec["atributo"], active, inactive

    def resolve(self, text, missing):
        """Replace placeholders in one string. Unknown names are appended to missing."""

        def swap(match):
            try:
                return str(self.value(match.group(1), match.group(2), match.group(3)))
            except ConfigError as exc:
                missing.append(str(exc))
                return match.group(0)

        return PLACEHOLDER.sub(swap, text)


REF_PREFIX = "@"


def resolve_refs(obj, env, env_path):
    """Replace each "@a.b.c" string with the value at that path in the environment file.

    Lets a client config point at the IDs the deploy pipeline already keeps in its
    environment file, instead of copying them.
    """
    if isinstance(obj, dict):
        return {key: resolve_refs(value, env, env_path) for key, value in obj.items()}
    if isinstance(obj, list):
        return [resolve_refs(value, env, env_path) for value in obj]
    if isinstance(obj, str) and obj.startswith(REF_PREFIX):
        current = env
        for part in obj[len(REF_PREFIX):].split("."):
            if not isinstance(current, dict) or part not in current:
                raise ConfigError(f"referencia {obj} nao existe em {env_path}")
            current = current[part]
        return current
    return obj


def _text(text, config, missing):
    if config is None or "{{" not in text:
        return text
    return config.resolve(text, missing)


def resolve_tree(obj, config, missing):
    """Resolve placeholders in the keys and string values of a nested object."""
    if isinstance(obj, dict):
        return {_text(k, config, missing): resolve_tree(v, config, missing) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_tree(v, config, missing) for v in obj]
    if isinstance(obj, str):
        return _text(obj, config, missing)
    return obj


def iter_strings(obj):
    """Yield every key and string value in a nested object."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from iter_strings(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_strings(item)
    elif isinstance(obj, str):
        yield obj


def iter_placeholders(obj):
    """Yield (kind, name, sub) for every placeholder in a nested object."""
    for text in iter_strings(obj):
        for match in PLACEHOLDER.finditer(text):
            yield match.group(1), match.group(2), match.group(3)

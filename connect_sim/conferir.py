"""Check the downloaded resources against the config and the local files. No AWS call.

Reads the export folder named by the config ('exportacao'), written by 'baixar'.
Every difference becomes a failure key: the text of its line, without indentation.
Keys listed in the config's 'divergencias_aceitas' file are known and accepted; the
CLI counts only the others. 'conferir --aceitar-atuais' writes the current keys into
that file, so the next run only fails on a new difference.

Checks, for each resource that has an export file:
- View actions: each acao_* in the config must be a SubmitButton action in the view.
- View version: the version in the config must match the published one, or be 'latest'.
- View Dropdown (when an option catalog names this view): each option must exist by value.
- Module: the published module must match the local module file, block by block, and
  each CaseTemplateId in it must be a template in the config.
- Option catalog: each Compare operand on the catalog attribute in the published module
  must be an active option.
"""

import json
import unicodedata
from datetime import date

from .baixar import export_dir
from .config import ConfigError

REFERENCE_KEYS = ("CaseTemplateId", "LambdaFunctionARN", "FlowModuleId", "ComparisonValue")


def fold(text):
    """Text without accents and case, to pair values that differ only by those."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def codepoints(text):
    return " ".join(f"U+{ord(ch):04X}" for ch in text)


def near(value, candidates):
    return [c for c in sorted(candidates) if fold(c) == fold(value) and c != value]


def load(folder, filename):
    path = folder / filename
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def walk(obj, visit):
    if isinstance(obj, dict):
        visit(obj)
        for value in obj.values():
            walk(value, visit)
    elif isinstance(obj, list):
        for item in obj:
            walk(item, visit)


def dropdown_values(template):
    values = []

    def visit(node):
        options = node.get("Options")
        for opt in options if isinstance(options, list) else []:
            if isinstance(opt, dict):
                value = opt.get("Value", opt.get("Label"))
                if value is not None and str(value) not in values:
                    values.append(str(value))

    walk(template, visit)
    return values


def submit_actions(template):
    found = []

    def visit(node):
        if node.get("Type") == "SubmitButton":
            props = node.get("Props") or {}
            if props.get("Action"):
                found.append((props["Action"], props.get("Label")))

    walk(template, visit)
    return dict(found)


class Report:
    """Collects the printed lines and the failure keys in one place."""

    def __init__(self):
        self.lines = []
        self.falhas = []

    def ok(self, text, indent=""):
        self.lines.append(indent + text)

    def fail(self, text, indent=""):
        self.lines.append(indent + text)
        self.falhas.append(text)


def check_view(name, config, folder, rep):
    template = load(folder, f"view-{name}-template.json")
    view = load(folder, f"view-{name}.json")
    if template is None or view is None:
        rep.fail(f"view {name}: sem export; rode connect-sim baixar com um flow que a use")
        return
    answered = submit_actions(template)
    for action in sorted(config.view_actions(name) or set()):
        if action in answered:
            rep.ok(f"view {name} acao {action!r}: OK (SubmitButton, rotulo {answered[action]!r})")
        else:
            rep.fail(f"view {name} acao {action!r}: FALTA; a view tem os botoes {sorted(answered)}")

    versions = load(folder, f"view-{name}-versoes.json") or []
    numbers = [v.get("Version") for v in versions if isinstance(v.get("Version"), int)]
    published = max(numbers) if numbers else None
    wanted = str(config.views[name].get("version", "latest"))
    if published is None:
        rep.fail(f"view {name}: export sem lista de versoes; rode connect-sim baixar para ver a versao publicada")
    elif wanted == "latest":
        rep.ok(f"view {name}: versao = latest, publicada agora {published}")
    elif str(published) == wanted:
        rep.ok(f"view {name}: versao {wanted} OK")
    else:
        rep.fail(f"view {name}: config diz {wanted}, publicada {published}")

    for key, spec in config.opcoes.items():
        if spec.get("view") != name:
            continue
        _, active, _inactive = config.option_values(key)
        values = dropdown_values(template)
        missing = [v for v in values if v not in active]
        rep.ok(f"view {name} Dropdown: {len(values)} opcoes, sem opcao igual em {key}: {len(missing)}")
        for value in missing:
            hits = near(value, active)
            if hits:
                rep.fail(f"ACENTO/CAIXA {value!r}, o catalogo tem {hits[0]!r}", indent="  ")
                rep.ok(f"codepoints: {codepoints(value)}", indent="        ")
            else:
                rep.fail(f"SEM PAR {value!r}", indent="  ")


def check_options_in_module(name, config, folder, rep):
    """Every Compare on a catalog attribute in the published module must test an active option."""
    published = load(folder, f"modulo-{name}.json")
    if published is None:
        return
    for key, spec in config.opcoes.items():
        _, active, _inactive = config.option_values(key)
        operands = []
        for action in published["Actions"]:
            if (action.get("Parameters") or {}).get("ComparisonValue") != f"$.Attributes.{spec['atributo']}":
                continue
            for cond in action.get("Transitions", {}).get("Conditions", []):
                for op in cond.get("Condition", {}).get("Operands", []):
                    if op not in operands:
                        operands.append(op)
        bad = [op for op in operands if op not in active]
        rep.ok(f"modulo {name} Compare em {spec['atributo']}: {len(operands)} operandos, fora do catalogo: {len(bad)}")
        for op in bad:
            hits = near(op, active)
            detail = f", o catalogo tem {hits[0]!r}" if hits else ", sem par no catalogo"
            rep.fail(f"FALTA {op!r}{detail}", indent="  ")


def check_module(name, config, folder, rep):
    published = load(folder, f"modulo-{name}.json")
    if published is None:
        rep.fail(f"modulo {name}: sem export; rode connect-sim baixar com um flow que o use")
        return
    local_path = config.base_dir / config.modules[name]["file"]
    if not local_path.is_file():
        rep.fail(f"modulo {name}: arquivo local nao existe: {local_path}")
        return
    local = json.loads(local_path.read_text(encoding="utf-8"))
    pub = {a["Identifier"]: a for a in published["Actions"]}
    loc = {a["Identifier"]: a for a in local["Actions"]}
    rep.ok(f"modulo {name}: publicado {len(pub)} blocos, local {len(loc)} blocos")
    for ident in sorted(set(pub) - set(loc)):
        rep.fail(f"so no publicado: {ident}", indent="  ")
    for ident in sorted(set(loc) - set(pub)):
        rep.fail(f"so no local: {ident}", indent="  ")
    for ident in sorted(set(pub) & set(loc)):
        if pub[ident].get("Type") != loc[ident].get("Type"):
            rep.fail(f"tipo diferente em {ident}: {pub[ident].get('Type')} x {loc[ident].get('Type')}", indent="  ")
            continue
        for key in REFERENCE_KEYS:
            a = (pub[ident].get("Parameters") or {}).get(key)
            b = (loc[ident].get("Parameters") or {}).get(key)
            if a != b:
                rep.fail(f"{key} diferente em {ident}: publicado {a!r}, local {b!r}", indent="  ")
    if published.get("StartAction") != local.get("StartAction"):
        rep.fail(f"StartAction: publicado {published.get('StartAction')!r}, local {local.get('StartAction')!r}", indent="  ")
    for ident, action in pub.items():
        tid = (action.get("Parameters") or {}).get("CaseTemplateId")
        if tid is None:
            continue
        ref = config.ref_for(tid)
        if ref is None or ref[0] != "template":
            rep.fail(f"template {tid} de {ident} nao esta no config", indent="  ")


def conferir(config):
    """Return (lines, falhas): the printed lines, and the key of every failure found."""
    folder = export_dir(config)
    if not folder.is_dir():
        raise ConfigError(f"pasta de export nao existe ainda: {folder} (rode connect-sim baixar)")
    rep = Report()
    for name in config.views:
        check_view(name, config, folder, rep)
    for name in config.modules:
        check_module(name, config, folder, rep)
        check_options_in_module(name, config, folder, rep)
    return rep.lines, rep.falhas


def gravar_aceitas(config, falhas):
    """Add the current failure keys to the accepted file, keeping the notes already there."""
    if not config.divergencias_arquivo:
        raise ConfigError("config sem 'divergencias_aceitas': diga em qual arquivo as divergencias aceitas ficam")
    path = config.base_dir / config.divergencias_arquivo
    existentes = {}
    if path.is_file():
        for item in json.loads(path.read_text(encoding="utf-8")).get("aceitas", []):
            existentes[item["chave"]] = item
    hoje = date.today().isoformat()
    for chave in sorted(set(falhas)):
        existentes.setdefault(chave, {"chave": chave, "motivo": f"aceita em {hoje}: divergencia conhecida, ainda nao verificada"})
    path.write_text(json.dumps({"aceitas": list(existentes.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

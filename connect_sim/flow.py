"""Load a Flow language file and check it before a simulated run.

Structural rules follow the Flow language reference in the connect-flow-language
steering catalog. The per-Action rules here come from the Action pages read with
get_action_doc: allowed error types, parameter ranges, and condition limits.
Action types without a verified rule get the structural checks only.

The check also reads the local config. It confirms that a ShowView condition names
an action the view answers, that a Compare on a catalog attribute tests only
active options from the local export, and it runs the same checks on every module
the flow calls. None of this calls AWS.
"""

import json
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .config import PLACEHOLDER, ConfigError, iter_placeholders, iter_strings

MAX_MODULE_DEPTH = 5

ACTION_TYPES = frozenset({
    "CompleteOutboundCall", "CreateCase", "CreateTask", "CreateWisdomSession",
    "DequeueContactAndTransferToQueue", "EndFlowModuleExecution", "GetCase",
    "InvokeFlowModule", "ResumeContact", "StartOutboundChatContact", "TagContact",
    "TransferContactToAgent", "TransferContactToQueue", "UnTagContact", "UpdateCase",
    "UpdateContactAttributes", "UpdateContactCallbackNumber", "UpdateContactData",
    "UpdateContactEventHooks", "UpdateContactMediaProcessing",
    "UpdateContactMediaStreamingBehavior", "UpdateContactRecordingAndAnalyticsBehavior",
    "UpdateContactRecordingBehavior", "UpdateContactRoutingBehavior",
    "UpdateContactTargetQueue", "UpdateContactTextToSpeechVoice",
    "UpdatePreviousContactParticipantState", "CheckHoursOfOperation", "CheckMetricData",
    "CheckOutboundCallStatus", "CheckVoiceId", "Compare", "DistributeByPercentage",
    "EndFlowExecution", "GetMetricData", "Loop", "StartVoiceIdStream", "TransferToFlow",
    "UpdateFlowAttributes", "UpdateFlowLoggingBehavior", "UpdateRoutingCriteria", "Wait",
    "AssociateContactToCustomerProfile", "CreateCallbackContact", "CreateCustomerProfile",
    "GetCalculatedAttributesForCustomerProfile", "GetCustomerProfile",
    "GetCustomerProfileObject", "InvokeLambdaFunction", "UpdateCustomerProfile",
    "ConnectParticipantWithLexBot", "DisconnectParticipant", "GetParticipantInput",
    "MessageParticipant", "MessageParticipantIteratively", "ShowView",
})

TERMINAL_TYPES = frozenset({"EndFlowExecution", "DisconnectParticipant", "EndFlowModuleExecution"})

ALLOWED_ERRORS = {
    "InvokeLambdaFunction": frozenset({"NoMatchingError"}),
    "CreateCase": frozenset({"ContactNotLinked", "NoMatchingError"}),
    "GetParticipantInput": frozenset({
        "NoMatchingCondition", "NoMatchingError", "InvalidPhoneNumber", "InputTimeLimitExceeded",
    }),
    "ShowView": frozenset({"NoMatchingError", "NoMatchingCondition", "TimeLimitExceeded"}),
    "Compare": frozenset({"NoMatchingCondition"}),
    "TransferContactToQueue": frozenset({"QueueAtCapacity", "NoMatchingError"}),
}

OPERATORS = frozenset({
    "Equals", "TextStartsWith", "TextEndsWith", "TextContains", "NumberGreaterThan",
    "NumberGreaterOrEqualTo", "NumberLessThan", "NumberLessOrEqualTo",
})

FORBIDDEN_CHARS = frozenset("%:()\\/=$,;[]{}")
FORBIDDEN_IDS = frozenset({
    "__proto__", "constructor", "__defineGetter__", "__defineSetter__", "toString",
    "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable", "toLocaleString", "valueOf",
})


@dataclass(frozen=True)
class Finding:
    """One check result. Level is ERROR, WARN or INFO; only ERROR blocks a run."""

    level: str
    where: str
    message: str


@dataclass
class Report:
    findings: list = field(default_factory=list)
    used: set = field(default_factory=set)

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "ERROR"]


class Flow:
    """A flow file loaded as data, with its Actions indexed by Identifier."""

    def __init__(self, path):
        self.path = Path(path)
        self.name = self.path.stem
        with open(self.path, encoding="utf-8") as fh:
            self.raw = json.load(fh)
        self.start = self.raw.get("StartAction")
        self.action_list = self.raw.get("Actions", [])
        self.actions = {}
        for action in self.action_list:
            self.actions.setdefault(action.get("Identifier"), action)


def _is_int_between(raw, low, high):
    try:
        return low <= int(str(raw)) <= high
    except ValueError:
        return False


def _successors(action):
    transitions = action.get("Transitions")
    if not isinstance(transitions, dict):
        return []
    targets = [transitions.get("NextAction")]
    targets += [e.get("NextAction") for e in transitions.get("Errors", [])]
    targets += [c.get("NextAction") for c in transitions.get("Conditions", [])]
    return [t for t in targets if t]


def _fold(text):
    """Return text without accents and case, so options that differ only by those can be paired."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _check_identifier(ident, seen, add):
    if ident in seen:
        add("ERROR", ident, "Identifier duplicado")
    seen.add(ident)
    if len(ident) > 50:
        add("ERROR", ident, "Identifier com mais de 50 caracteres")
    bad = sorted(set(ident) & FORBIDDEN_CHARS)
    if bad:
        add("ERROR", ident, "caracteres proibidos no Identifier: " + " ".join(bad))
    if ident in FORBIDDEN_IDS:
        add("ERROR", ident, "Identifier reservado pelo Connect")


def _check_transitions(ident, kind, transitions, flow, add):
    errors = transitions.get("Errors", [])
    conditions = transitions.get("Conditions", [])
    if not isinstance(errors, list) or not isinstance(conditions, list):
        add("ERROR", ident, "Errors e Conditions precisam ser listas")
        return
    targets = [transitions["NextAction"]] if "NextAction" in transitions else []
    for err in errors:
        if not err.get("NextAction"):
            add("ERROR", ident, f"ramo {err.get('ErrorType')} sem NextAction")
        targets.append(err.get("NextAction"))
    for cond in conditions:
        if not cond.get("NextAction"):
            add("ERROR", ident, "Condition sem NextAction")
        targets.append(cond.get("NextAction"))
        condition = cond.get("Condition", {})
        if condition.get("Operator") not in OPERATORS:
            add("ERROR", ident, f"operador invalido: {condition.get('Operator')!r}")
        if len(condition.get("Operands", [])) != 1:
            add("ERROR", ident, "Operands deve ter exatamente um valor")
    for target in targets:
        if target and target not in flow.actions:
            add("ERROR", ident, f"aponta para '{target}', que nao existe no fluxo")
    if kind not in TERMINAL_TYPES and not any(targets):
        add("ERROR", ident, "sem NextAction, Errors nem Conditions; a execucao nao tem como seguir")

    error_types = {err.get("ErrorType") for err in errors}
    allowed = ALLOWED_ERRORS.get(kind)
    if allowed is not None:
        for etype in sorted(error_types - allowed):
            add("ERROR", ident, f"erro '{etype}' nao existe para {kind}; aceitos: {', '.join(sorted(allowed))}")
    if kind == "GetParticipantInput" and "NoMatchingError" not in error_types:
        add("ERROR", ident, "GetParticipantInput exige o ramo NoMatchingError")
    if kind == "Compare" and conditions and "NoMatchingCondition" not in error_types:
        add("WARN", ident, "Compare sem ramo NoMatchingCondition; valor fora das condicoes nao tem destino")
    if kind == "InvokeLambdaFunction" and conditions:
        add("ERROR", ident, "InvokeLambdaFunction nao aceita Conditions; leia o retorno em $.External")


def _check_parameters(ident, kind, params, transitions, add):
    if not isinstance(params, dict):
        add("ERROR", ident, "Parameters precisa ser objeto")
        return
    if kind == "InvokeLambdaFunction":
        if "LambdaFunctionARN" not in params:
            add("ERROR", ident, "falta LambdaFunctionARN")
        raw = params.get("InvocationTimeLimitSeconds")
        if not _is_int_between(raw, 1, 8):
            add("ERROR", ident, f"InvocationTimeLimitSeconds deve ser inteiro de 1 a 8; recebeu {raw!r}")
        response_type = (params.get("ResponseValidation") or {}).get("ResponseType")
        if response_type not in ("STRING_MAP", "JSON"):
            add("WARN", ident, "ResponseValidation ausente ou invalido")
    elif kind == "ShowView":
        view = params.get("ViewResource") or {}
        if not view.get("Id") or not view.get("Version"):
            add("ERROR", ident, "ShowView precisa de ViewResource com Id e Version")
    elif kind == "UpdateContactAttributes":
        if not isinstance(params.get("Attributes"), dict):
            add("ERROR", ident, "Attributes ausente ou nao e objeto")
        if params.get("TargetContact") not in ("Current", "Related"):
            add("ERROR", ident, "TargetContact deve ser Current ou Related")
    elif kind == "GetParticipantInput":
        if str(params.get("StoreInput")).lower() == "true" and transitions.get("Conditions"):
            add("ERROR", ident, "com StoreInput True nao ha condicoes; use NextAction")
    elif kind == "UpdateContactTargetQueue":
        if not params.get("QueueId") and not params.get("AgentId"):
            add("ERROR", ident, "UpdateContactTargetQueue precisa de QueueId ou AgentId")
    elif kind == "CreateCase":
        if not params.get("CaseTemplateId"):
            add("ERROR", ident, "CreateCase precisa de CaseTemplateId")
    elif kind == "Compare":
        if "ComparisonValue" not in params:
            add("ERROR", ident, "Compare precisa de ComparisonValue")


def _check_references(ident, obj, config, used, add):
    for kind, name, sub in iter_placeholders(obj):
        used.add((kind, name, sub))
        if config is None:
            continue
        try:
            config.value(kind, name, sub)
        except ConfigError as exc:
            add("ERROR", ident, f"referencia sem valor no config: {exc}")
    for text in iter_strings(obj):
        if "{{" in text and not PLACEHOLDER.search(text):
            add("ERROR", ident, f"placeholder mal formado: {text}")


def _check_system_fields(ident, params, config, add):
    """Warn when a template's system field is not set by CreateCase."""
    match = PLACEHOLDER.fullmatch(str(params.get("CaseTemplateId", "")))
    if not match or match.group(1) != "template":
        return
    tmpl = config.templates.get(match.group(2))
    if tmpl is None:
        return
    given = {config.resolve(key, []) for key in (params.get("CaseRequestFields") or {})}
    for fname, fdef in tmpl.get("fields", {}).items():
        if fdef.get("system") and str(fdef.get("id")) not in given:
            add("WARN", ident, f"campo de sistema '{fname}' nao esta no CaseRequestFields; o caso sai com ele vazio")


def _check_view_actions(ident, params, transitions, config, add):
    """Each ShowView condition must name an action that the configured view answers."""
    view_id = str((params.get("ViewResource") or {}).get("Id", ""))
    match = PLACEHOLDER.fullmatch(view_id)
    if not match or match.group(1) != "view":
        return
    name = match.group(2)
    allowed = config.view_actions(name)
    if allowed is None:
        return  # a missing view is already reported by the reference check
    for cond in transitions.get("Conditions", []):
        operand = str((cond.get("Condition") or {}).get("Operands", [""])[0])
        resolved = config.resolve(operand, [])
        if resolved not in allowed:
            listed = ", ".join(sorted(allowed)) or "nenhuma acao configurada"
            add("ERROR", ident, f"acao {resolved!r} nao existe na view '{name}'; a view responde: {listed}")


def _check_compare_options(ident, params, transitions, config, add):
    """A Compare on a catalog attribute may test only options the local export lists as active."""
    value = params.get("ComparisonValue")
    if not isinstance(value, str):
        return
    for key, spec in config.opcoes.items():
        if value != f"$.Attributes.{spec.get('atributo')}":
            continue
        try:
            attribute, active, inactive = config.option_values(key)
        except ConfigError as exc:
            add("WARN", ident, f"checagem de opcoes pulada: {exc}")
            return
        for cond in transitions.get("Conditions", []):
            operand = str((cond.get("Condition") or {}).get("Operands", [""])[0])
            if operand in active:
                continue
            if operand in inactive:
                add("WARN", ident, f"{operand!r} e opcao INATIVA de {key}; este ramo nunca bate")
                continue
            near = [opt for opt in sorted(active | inactive) if _fold(opt) == _fold(operand)]
            hint = f"; a opcao real e {near[0]!r} (so acento ou caixa)" if near else ""
            add("ERROR", ident, f"{operand!r} nao e opcao de {key} ({attribute}){hint}")
        return


def _check_module(ident, params, config, report, depth, add):
    """Run the same checks on the module a block calls, so its rules are covered too."""
    match = PLACEHOLDER.fullmatch(str(params.get("FlowModuleId", "")))
    if not match or match.group(1) != "module":
        return
    entry = config.modules.get(match.group(2))
    if entry is None or "file" not in entry:
        return  # a missing module is already reported by the reference check
    path = config.base_dir / entry["file"]
    if not path.is_file():
        add("ERROR", ident, f"arquivo do modulo nao existe: {path}")
        return
    sub = Flow(path)
    sub_report = check(sub, config, depth + 1)
    for finding in sub_report.findings:
        add(finding.level, f"{sub.name}:{finding.where}", finding.message)
    report.used.update(sub_report.used)


def check(flow, config=None, depth=0):
    """Return a Report. ERROR findings block a simulated run."""
    report = Report()
    findings = report.findings

    def add(level, where, message):
        findings.append(Finding(level, where, message))

    if "Version" not in flow.raw:
        add("WARN", "topo", "campo Version ausente")
    if flow.start is None:
        add("ERROR", "topo", "campo StartAction ausente")

    seen = set()
    for action in flow.action_list:
        ident = action.get("Identifier")
        if not isinstance(ident, str) or not ident:
            add("ERROR", "(sem Identifier)", "Action sem Identifier")
            continue
        _check_identifier(ident, seen, add)
        kind = action.get("Type")
        if kind not in ACTION_TYPES:
            add("ERROR", ident, f"Type '{kind}' nao esta na lista de Actions do Connect")
        transitions = action.get("Transitions")
        if not isinstance(transitions, dict):
            add("ERROR", ident, "Transitions ausente ou nao e objeto")
            continue
        _check_transitions(ident, kind, transitions, flow, add)
        params = action.get("Parameters", {})
        _check_parameters(ident, kind, params, transitions, add)
        _check_references(ident, params, config, report.used, add)
        _check_references(ident, transitions, config, report.used, add)
        if config is None or not isinstance(params, dict):
            continue
        if kind == "CreateCase":
            _check_system_fields(ident, params, config, add)
        elif kind == "ShowView":
            _check_view_actions(ident, params, transitions, config, add)
        elif kind == "Compare":
            _check_compare_options(ident, params, transitions, config, add)
        elif kind == "InvokeFlowModule" and depth < MAX_MODULE_DEPTH:
            _check_module(ident, params, config, report, depth, add)

    if flow.start is not None and flow.start not in flow.actions:
        add("ERROR", "topo", f"StartAction '{flow.start}' nao existe no fluxo")
    elif flow.start is not None:
        reached = set()
        queue = deque([flow.start])
        while queue:
            ident = queue.popleft()
            if ident in reached or ident not in flow.actions:
                continue
            reached.add(ident)
            queue.extend(_successors(flow.actions[ident]))
        for ident in flow.actions:
            if ident not in reached:
                add("WARN", ident, "inalcancavel a partir do StartAction")
    return report


def unused_entries(config, used):
    """List config entries that no placeholder in the flow refers to."""
    names = {(kind, name) for kind, name, _sub in used}
    out = []
    for kind, table in (("queue", config.queues), ("view", config.views),
                        ("template", config.templates), ("lambda", config.lambdas)):
        for name in table:
            if (kind, name) not in names and not (kind == "template" and ("field", name) in names):
                out.append(f"{kind}.{name}")
    for tname, tmpl in config.templates.items():
        for fname in tmpl.get("fields", {}):
            if ("field", tname, fname) not in used:
                out.append(f"field.{tname}.{fname}")
    return out

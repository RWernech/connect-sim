"""Step through a flow one Action at a time, the way a contact moves through it.

Each handler returns a Step. The router then follows the Transitions of the
Action: a matching condition or an error branch wins, and NextAction is used
when neither applies. Behavior this simulator does not reproduce stops the run
with NotSimulated, so an unknown block is never assumed to succeed.
"""

import re
from dataclasses import dataclass, field

from . import resources
from .config import resolve_tree
from .flow import Flow

NO_RESULT = object()
MAX_STEPS = 200
NOT_SIMULATED = "NotSimulated"
NO_FALLBACK = {"NoMatchingCondition", NOT_SIMULATED}
# Blocos sem simulador que nao escolhem rota: ajustes de logging, voz e idioma.
# Com --pular-sem-efeito, o simulador pula esses blocos (seguindo o NextAction) e avisa no log.
# Qualquer outro bloco sem simulador continua parando com NotSimulated.
SEM_EFEITO_NA_ROTA = frozenset({
    "UpdateFlowLoggingBehavior",
    "UpdateContactTextToSpeechVoice",
    "UpdateContactData",
})
KNOWN_ROOTS = {"Attributes", "External", "StoredCustomerInput", "Views", "ContactData", "Case"}
DEFAULT_CONTACT = {
    "ContactId": "contato-simulado-0001",
    "Channel": "CHAT",
    "InitiationMethod": "INBOUND",
    "CustomerEndpoint": {"Address": "+12065550101", "Type": "TELEPHONE_NUMBER"},
}


@dataclass
class Step:
    """What one Action did. result feeds Conditions; error selects an error branch."""

    result: object = NO_RESULT
    error: str = None
    message: str = ""
    terminal: str = None
    notes: list = field(default_factory=list)


@dataclass
class RunResult:
    outcome: str
    path: list
    failed_at: str = None
    reason: str = ""
    attributes: dict = field(default_factory=dict)
    cases: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def _matches(cond, value):
    if value is None:
        return False
    operator = cond.get("Operator")
    operand = cond.get("Operands", [""])[0]
    text = str(value)
    if operator == "Equals":
        return text == operand
    if operator == "TextStartsWith":
        return text.startswith(operand)
    if operator == "TextEndsWith":
        return text.endswith(operand)
    if operator == "TextContains":
        return operand in text
    try:
        left, right = float(value), float(operand)
    except (TypeError, ValueError):
        return False
    comparisons = {
        "NumberGreaterThan": left > right,
        "NumberGreaterOrEqualTo": left >= right,
        "NumberLessThan": left < right,
        "NumberLessOrEqualTo": left <= right,
    }
    return comparisons.get(operator, False)


def _find_key(obj, key):
    """Return the first value stored under key, searching nested objects."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _find_route(errors, error_type):
    for err in errors:
        if err.get("ErrorType") == error_type:
            return err.get("NextAction")
    return None


class Engine:
    """Runs one flow against one client config and one scenario."""

    def __init__(self, flow, config, scenario, emit, skip=frozenset()):
        self.flow = flow
        self.config = config
        self.sc = scenario
        self.emit = emit
        self.attrs = dict(getattr(scenario, "attributes", {}))
        self.external = None
        self.stored_input = None
        self.view_data = {}
        self.target_queue = None
        self.inputs = list(scenario.inputs)
        self.views = {name: list(items) for name, items in scenario.views.items()}
        self.contact = {**DEFAULT_CONTACT, **scenario.contact}
        self.cases = resources.CaseStore(config)
        self._lambdas = {}
        self.last_case = {}
        self.depth = 0
        self.skip = frozenset(skip)
        self.skipped = []

    def run(self):
        path = []
        current = self.flow.start
        for seq in range(1, MAX_STEPS + 1):
            action = self.flow.actions.get(current)
            if action is None:
                return self._finish("unhandled_error", path, current, f"Action '{current}' nao existe no fluxo")
            path.append(current)
            missing = []
            params = resolve_tree(action.get("Parameters", {}), self.config, missing)
            transitions = resolve_tree(action.get("Transitions", {}), self.config, missing)
            if missing:
                return self._finish("config_error", path, current, "; ".join(missing))
            reads = []
            params = self._dyn(params, reads)
            step = self._execute(action, params)
            step.notes = reads + step.notes
            nxt = self._route(transitions, step)
            self.emit({
                "seq": seq,
                "action": current,
                "type": action.get("Type"),
                "message": step.message,
                "error": step.error,
                "next": nxt,
                "notes": step.notes,
            })
            if step.terminal:
                return self._finish(step.terminal, path)
            if nxt is None:
                reason = f"{step.error or 'sem NextAction'}: {step.message}"
                return self._finish("unhandled_error", path, current, reason)
            current = nxt
        return self._finish("loop_limit", path, current, f"mais de {MAX_STEPS} passos sem sair do fluxo")

    def _finish(self, outcome, path, failed_at=None, reason=""):
        return RunResult(outcome, list(path), failed_at, reason, dict(self.attrs), list(self.cases.cases),
                         list(self.skipped))

    def _route(self, transitions, step):
        """Pick the next Action from already resolved Transitions."""
        errors = transitions.get("Errors", [])
        conditions = transitions.get("Conditions", [])
        if step.error is None and step.result is not NO_RESULT and conditions:
            for cond in conditions:
                if _matches(cond["Condition"], step.result):
                    return cond["NextAction"]
            default = _find_route(errors, "NoMatchingCondition")
            if default is not None:
                step.message += " (ramo padrao NoMatchingCondition)"
                step.notes.append(
                    f"{step.result!r} nao bateu com nenhuma condicao; caiu no ramo padrao (sem caso ou sem destino explicito)"
                )
                return default
            step.error = "NoMatchingCondition"
            step.message = f"nenhuma condicao bateu com {step.result!r}"
        if step.error is None:
            return transitions.get("NextAction")
        handled = _find_route(errors, step.error)
        if handled is None and step.error not in NO_FALLBACK:
            handled = _find_route(errors, "NoMatchingError")
        return handled

    def _execute(self, action, params):
        kind = action.get("Type", "")
        if kind in self.skip:
            self.skipped.append(kind)
            return Step(message=f"pulado: {kind} sem simulador, sem efeito na rota (--pular-sem-efeito)",
                        notes=[f"{kind} pulado: nao escolhe rota; o simulador so segue o NextAction"])
        handler = getattr(self, "_do_" + kind, None)
        if handler is None:
            return Step(error=NOT_SIMULATED, message=f"bloco {kind} ainda nao tem simulador")
        return handler(params)

    def _dyn(self, obj, reads):
        """Replace $. paths with their current values. Empty values are noted."""
        if isinstance(obj, dict):
            return {k: self._dyn(v, reads) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._dyn(v, reads) for v in obj]
        if isinstance(obj, str) and obj.startswith("$.") and " $." in obj:
            return self._interpolate(obj, reads)
        if isinstance(obj, str) and obj.startswith("$."):
            root = obj[2:].split(".")[0]
            if root not in KNOWN_ROOTS:
                reads.append(f"{obj} nao e simulado")
                return None
            value = self._path(obj)
            if value is None and not self._opcional(obj):
                reads.append(f"{obj} esta vazio")
            return value
        return obj

    def _interpolate(self, text, reads):
        """Join several $. references in one value, e.g. "$.External.firstName $.External.lastName".

        The simulator fills each reference with its value and joins the text. Connect's
        handling of this form is not confirmed, so the run always notes it.
        """
        refs = re.findall(r"\$\.[A-Za-z0-9_.\-]+", text)
        reads.append(f"{text!r} junta {len(refs)} caminhos; no Connect esse formato nao esta confirmado")
        out = text
        for ref in refs:
            root = ref[2:].split(".")[0]
            if root not in KNOWN_ROOTS:
                reads.append(f"{ref} nao e simulado")
                value = ""
            else:
                value = self._path(ref)
                if value is None:
                    reads.append(f"{ref} esta vazio")
                    value = ""
            out = out.replace(ref, str(value), 1)
        return out.strip()

    def _opcional(self, expr):
        """True when the path reads an attribute the config marks as optional (may stay empty)."""
        prefix = "$.Attributes."
        return expr.startswith(prefix) and expr[len(prefix):] in self.config.opcionais

    def _path(self, expr):
        parts = expr[2:].split(".")
        root, rest = parts[0], parts[1:]
        if root == "Attributes":
            return self.attrs.get(".".join(rest))
        if root == "External":
            return (self.external or {}).get(".".join(rest))
        if root == "StoredCustomerInput":
            return self.stored_input
        if root == "Views" and rest[:1] == ["ViewResultData"]:
            return self.view_data.get(".".join(rest[1:]))
        if root == "ContactData":
            return self.contact.get(".".join(rest))
        if root == "Case":
            return self.last_case.get(".".join(rest))
        return None

    # Handlers. Each one takes resolved Parameters and returns a Step.

    def _do_UpdateContactAttributes(self, params):
        attrs = params.get("Attributes", {})
        notes = []
        for key, value in attrs.items():
            if value is None and key not in self.config.opcionais:
                notes.append(f"atributo {key} recebe valor vazio")
            self.attrs[key] = value
        if params.get("TargetContact") == "Related":
            notes.append("TargetContact Related nao e simulado; gravado no contato atual")
        summary = ", ".join(f"{k}={v!r}" for k, v in attrs.items())
        return Step(message=f"grava {summary}", notes=notes)

    def _do_MessageParticipant(self, params):
        text = params.get("Text") or f"prompt {params.get('PromptId')}"
        return Step(message=f"cliente recebe: {text}")

    def _do_GetParticipantInput(self, params):
        if not self.inputs:
            return Step(error="InputTimeLimitExceeded", message="cliente nao respondeu (roteiro vazio)")
        value = self.inputs.pop(0)
        if str(params.get("StoreInput", "False")).lower() == "true":
            self.stored_input = value
            return Step(message=f"entrada gravada em $.StoredCustomerInput: {value!r}")
        return Step(result=value, message=f"entrada: {value!r}")

    def _do_InvokeLambdaFunction(self, params):
        arn = params.get("LambdaFunctionARN")
        ref = self.config.ref_for(arn)
        if ref is None or ref[0] != "lambda":
            return Step(error="NoMatchingError", message=f"ARN {arn} nao esta no config")
        name = ref[1]
        override = self.sc.lambdas.get(name, {})
        if "fail" in override:
            return Step(error="NoMatchingError", message=f"{name} falhou (cenario): {override['fail']}")
        event = {
            "Details": {
                "ContactData": self.contact,
                "Parameters": params.get("LambdaInvocationAttributes", {}),
            },
            "Name": "ContactFlowEvent",
        }
        if "response" in override:
            response = override["response"]
        else:
            try:
                response = self._local_handler(name)(event, None)
            except Exception as exc:  # the Lambda raised; Connect routes this to NoMatchingError
                return Step(error="NoMatchingError", message=f"{name} lancou {type(exc).__name__}: {exc}")
        if (params.get("ResponseValidation") or {}).get("ResponseType") == "STRING_MAP":
            if not isinstance(response, dict) or any(not isinstance(v, str) for v in response.values()):
                return Step(error="NoMatchingError", message=f"{name} nao devolveu um mapa de strings")
        notes = []
        if self.external:
            lost = sorted(set(self.external) - set(response))
            if lost:
                notes.append(f"$.External substituido; chaves da chamada anterior perdidas: {', '.join(lost)}")
        self.external = dict(response)
        return Step(message=f"{name} devolveu {sorted(response)}", notes=notes)

    def _local_handler(self, name):
        if name not in self._lambdas:
            entry = self.config.lambdas[name]
            path = self.config.base_dir / entry["local"]
            self._lambdas[name] = resources.load_handler(path, entry.get("function", "handler"))
        return self._lambdas[name]

    def _do_ShowView(self, params):
        view = params.get("ViewResource") or {}
        ref = self.config.ref_for(view.get("Id"))
        if ref is None or ref[0] != "view":
            return Step(error="NoMatchingError", message="view nao esta no config")
        name = ref[1]
        for key, value in (params.get("ViewData") or {}).items():
            if value is None:
                return Step(
                    error="NoMatchingError",
                    message=f"view '{name}' quebra no primeiro render: '{key}' aponta para atributo que ainda nao existe",
                )
        queue = self.views.get(name, [])
        if not queue:
            return Step(error="TimeLimitExceeded", message=f"view '{name}' sem resposta do agente (roteiro vazio)")
        answer = queue.pop(0)
        self.view_data = dict(answer.get("data", {}))
        return Step(result=answer.get("action"), message=f"view '{name}' respondeu acao {answer.get('action')!r}")

    def _do_Compare(self, params):
        value = params.get("ComparisonValue")
        return Step(result=value, message=f"compara {value!r}")

    def _do_UpdateContactTargetQueue(self, params):
        if params.get("AgentId"):
            return Step(error=NOT_SIMULATED, message="UpdateContactTargetQueue com AgentId nao e simulado")
        ref = self.config.ref_for(params.get("QueueId"))
        if ref is None or ref[0] != "queue":
            return Step(error="NoMatchingError", message="fila nao esta no config")
        self.target_queue = ref[1]
        return Step(message=f"fila alvo: {ref[1]}")

    def _do_TransferContactToQueue(self, params):
        if self.target_queue is None:
            return Step(error="NoMatchingError", message="TargetQueue nao foi definida antes do transfer")
        if self.sc.queues.get(self.target_queue, {}).get("at_capacity"):
            return Step(error="QueueAtCapacity", message=f"fila '{self.target_queue}' lotada (cenario)")
        return Step(message=f"contato colocado na fila '{self.target_queue}'")

    def _do_CreateCase(self, params):
        ref = self.config.ref_for(params.get("CaseTemplateId"))
        if ref is None or ref[0] != "template":
            return Step(error="NoMatchingError", message="template de caso nao esta no config")
        template = ref[1]
        fields, notes = {}, []
        for key_id, value in (params.get("CaseRequestFields") or {}).items():
            fref = self.config.ref_for(key_id)
            if fref is not None and fref[0] == "field" and fref[1] == template:
                fields[fref[2]] = value
            else:
                fields[key_id] = value
        case_id, problems, more = self.cases.create(template, fields)
        notes += more
        if problems:
            return Step(error="NoMatchingError", message="; ".join(problems), notes=notes)
        self.last_case = {"case_id": case_id, "reference_number": f"RN-{case_id}"}
        if str(params.get("LinkContactToCase", "false")).lower() == "true" and self.sc.case_link_fails:
            return Step(
                error="ContactNotLinked",
                message=f"caso {case_id} criado, contato nao vinculado (cenario)",
                notes=notes,
            )
        return Step(message=f"caso {case_id} criado no template '{template}'", notes=notes)

    def _do_DisconnectParticipant(self, params):
        return Step(terminal="disconnected", message="desconecta o cliente")

    def _do_EndFlowExecution(self, params):
        return Step(terminal="end_flow", message="encerra o fluxo")

    def _do_EndFlowModuleExecution(self, params):
        return Step(terminal="module_end", message="retorna ao fluxo chamador")

    def _do_InvokeFlowModule(self, params):
        if self.depth >= 5:
            return Step(error="NoMatchingError", message="modulos aninhados demais (mais de 5 niveis)")
        ref = self.config.ref_for(params.get("FlowModuleId"))
        if ref is None or ref[0] != "module":
            return Step(error="NoMatchingError", message=f"modulo {params.get('FlowModuleId')} nao esta no config")
        name = ref[1]
        entry = self.config.modules[name]
        sub = Engine(Flow(self.config.base_dir / entry["file"]), self.config, self.sc, self.emit)
        sub.attrs, sub.inputs, sub.views = self.attrs, self.inputs, self.views
        sub.cases, sub.contact, sub.last_case = self.cases, self.contact, self.last_case
        sub.external, sub.stored_input, sub.view_data = self.external, self.stored_input, self.view_data
        sub.target_queue, sub.depth = self.target_queue, self.depth + 1
        result = sub.run()
        self.external, self.stored_input, self.view_data = sub.external, sub.stored_input, sub.view_data
        self.target_queue, self.last_case = sub.target_queue, sub.last_case
        if result.outcome != "module_end":
            return Step(error="NoMatchingError", message=f"modulo '{name}' terminou em {result.outcome}: {result.reason}")
        return Step(message=f"modulo '{name}' retornou ({len(result.path)} blocos)")

    def _do_Wait(self, params):
        return Step(result="WaitCompleted", message="espera simulada (sem tempo real)")

    def _do_GetCustomerProfile(self, params):
        if self.sc.profile == "none":
            return Step(error="NoneFoundError", message="perfil nao encontrado (cenario)")
        if self.sc.profile == "multiple":
            return Step(error="MultipleFoundError", message="mais de um perfil (cenario)")
        return Step(message=f"perfil encontrado para {_find_key(params, 'IdentifierValue')!r}")

    def _do_GetCase(self, params):
        case_id = (params.get("CaseRequestFields") or {}).get("case_id")
        case = self.cases.get(case_id)
        if case is None:
            return Step(error="NoneFound", message=f"caso {case_id!r} nao encontrado")
        self.last_case = {"case_id": case["id"], "reference_number": case["reference_number"]}
        return Step(message=f"caso {case['id']} lido; reference_number={case['reference_number']}")

    def _do_UpdateCase(self, params):
        case_id = params.get("CaseId")
        if self.cases.get(case_id) is None:
            return Step(error="NoMatchingError", message=f"caso {case_id!r} nao existe para atualizar")
        fields = dict(params.get("CaseRequestFields") or {})
        self.cases.update(case_id, fields)
        return Step(message=f"caso {case_id} atualizado: {sorted(fields)}")

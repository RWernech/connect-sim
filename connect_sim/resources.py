"""Stand-ins for the resources a flow calls: cases and local Lambda handlers."""

import importlib.util
from pathlib import Path


class CaseStore:
    """In-memory cases. Validates fields against the template when the config defines them."""

    def __init__(self, config):
        self.config = config
        self.cases = []

    def get(self, case_id):
        for case in self.cases:
            if case["id"] == case_id:
                return case
        return None

    def create(self, template, values):
        """Return (case_id, problems, notes). Problems block the case; notes do not."""
        spec = self.config.templates[template].get("fields", {})
        problems, notes = [], []
        for fname in values:
            if fname not in spec:
                notes.append(f"campo '{fname}' fora do config do template; valor nao validado")
        for fname, fdef in spec.items():
            if fdef.get("system"):
                if fname not in values:
                    notes.append(f"campo de sistema '{fname}' ficou vazio; o fluxo nao preenche")
                continue
            value = values.get(fname)
            if value in (None, ""):
                if fdef.get("required"):
                    problems.append(f"campo obrigatorio vazio: '{fname}'")
                continue
            if fdef.get("type") == "single_select":
                options = fdef.get("options")
                if options is None:
                    notes.append(f"'{fname}' sem lista de opcoes no config; valor nao validado")
                elif value not in options:
                    problems.append(f"'{value}' fora das opcoes de '{fname}'")
            max_len = fdef.get("max_length")
            if max_len and len(str(value)) > max_len:
                problems.append(f"'{fname}' passa de {max_len} caracteres")
        if problems:
            return None, problems, notes
        case_id = f"case-{len(self.cases) + 1:04d}"
        self.cases.append({
            "id": case_id,
            "template": template,
            "fields": dict(values),
            "reference_number": f"RN-{case_id}",
        })
        return case_id, [], notes

    def update(self, case_id, fields):
        """Store the fields on an existing case. Keys are kept as given."""
        case = self.get(case_id)
        case["fields"].update(fields)
        return []


def load_handler(path, function_name):
    """Import a local Lambda file and return one of its functions."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"handler local nao encontrado: {path}")
    spec = importlib.util.spec_from_file_location(f"connect_sim_lambda_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function_name)

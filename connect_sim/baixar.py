"""Download the AWS resources that a test needs. Read-only, and only into the client folder.

The download list comes from the flows passed as parameters: every view, module and
template a flow reaches through a placeholder, every option catalog in the config,
and the test agent when the config names one. Nothing else is read.

Where things come from:
- Account, profile, region, Connect instance and Cases domain: the client environment
  file passed with --ambiente (ex.: work/<cliente>/config/stg.json). The same file
  the deploy pipeline reads, so there is one source for those values.
- Resource IDs: the config passed with --config.

Where files go: the folder named by the config's 'exportacao'. It must sit inside
work/<cliente>/. If it does not, the command stops before any AWS call.

--dry-run prints the list and makes no AWS call, so the list can be checked first.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError
from .flow import check

KINDS = ("view", "module", "template")


def load_ambiente(path):
    """Read the client environment file and return the values the downloads need."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"arquivo de ambiente nao existe: {path}")
    env = json.loads(path.read_text(encoding="utf-8"))
    try:
        return {
            "environment": env["environment"],
            "profile": env["ssoProfile"],
            "region": env["region"],
            "instance": env["connect"]["instanceId"],
            "domain": env["cases"]["domainId"],
        }
    except KeyError as exc:
        raise ConfigError(f"arquivo de ambiente sem a chave {exc}: {path}") from exc


def client_root(config):
    """Return work/<cliente>, the only folder a download may write into."""
    root = config.base_dir.parent
    if root.parent.name != "work":
        raise ConfigError(f"config fora de work/<cliente>/config: {config.path}")
    return root


def export_dir(config):
    """Return the export folder, after checking it is inside the client folder."""
    root = client_root(config).resolve()
    target = config.export_path("_").parent
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConfigError(f"'exportacao' aponta para fora de {root}: {target}") from exc
    return target


def templates_of_module(config, name):
    """Return the config names of the case templates a module creates. Module files hold literal IDs."""
    entry = config.modules.get(name) or {}
    path = config.base_dir / entry.get("file", "")
    if not path.is_file():
        return []
    found = []
    with open(path, encoding="utf-8") as fh:
        module = json.load(fh)
    for action in module.get("Actions", []):
        ref = config.ref_for((action.get("Parameters") or {}).get("CaseTemplateId", ""))
        if ref is not None and ref[0] == "template" and ref[1] not in found:
            found.append(ref[1])
    return found


def plan(config, flows):
    """Return the ordered (kind, name) pairs to download for these flows."""
    used = set()
    for flow in flows:
        used |= check(flow, config).used
    items = []
    for kind, name, _sub in sorted(used, key=lambda t: (t[0], t[1], t[2] or "")):
        if kind in KINDS and (kind, name) not in items:
            items.append((kind, name))
    for kind, name in list(items):
        if kind == "module":
            for tname in templates_of_module(config, name):
                if ("template", tname) not in items:
                    items.append(("template", tname))
    for key in config.opcoes:
        items.append(("opcoes", key))
    agent = str(config.agente.get("usuario", "")).strip()
    if agent and "<" not in agent:
        items.append(("agente", "agente_teste"))
    return items


class Downloader:
    """Makes the read-only AWS calls. Created only when a real download runs."""

    def __init__(self, config, env, out):
        try:
            import boto3
        except ImportError as exc:
            raise ConfigError("boto3 nao instalado; rode com: uv run --extra aws -m connect_sim baixar ...") from exc
        try:
            session = boto3.Session(profile_name=env["profile"], region_name=env["region"])
            self.connect = session.client("connect")
            self.cases = session.client("connectcases")
        except Exception as exc:
            raise ConfigError(
                f"sessao AWS '{env['profile']}' nao abriu ({type(exc).__name__}: {exc}); "
                f"rode aws sso login --profile {env['profile']}"
            ) from exc
        self.config, self.env, self.out = config, env, out

    def save(self, filename, data):
        self.out.mkdir(parents=True, exist_ok=True)
        path = self.out / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return filename

    def get(self, kind, name):
        return getattr(self, kind)(name)

    def view(self, name):
        entry = self.config.views[name]
        view = self.connect.describe_view(InstanceId=self.env["instance"], ViewId=entry["id"])["View"]
        files = [self.save(f"view-{name}.json", view)]
        template = (view.get("Content") or {}).get("Template")
        if isinstance(template, str):
            files.append(self.save(f"view-{name}-template.json", json.loads(template)))
        versions = []
        token = None
        while True:
            kwargs = {"InstanceId": self.env["instance"], "ViewId": entry["id"], "MaxResults": 100}
            if token:
                kwargs["NextToken"] = token
            page = self.connect.list_view_versions(**kwargs)
            versions.extend(page.get("ViewVersionSummaryList", []))
            token = page.get("NextToken")
            if not token:
                break
        files.append(self.save(f"view-{name}-versoes.json", versions))
        numbers = [v.get("Version") for v in versions if isinstance(v.get("Version"), int)]
        latest = max(numbers) if numbers else None
        return {"versao_publicada": latest, "arquivos": files}

    def module(self, name):
        entry = self.config.modules[name]
        mod = self.connect.describe_contact_flow_module(
            InstanceId=self.env["instance"], ContactFlowModuleId=entry["id"])["ContactFlowModule"]
        content = json.loads(mod["Content"])
        return {"estado": mod.get("State"), "status": mod.get("Status"),
                "arquivos": [self.save(f"modulo-{name}.json", content)]}

    def template(self, name):
        entry = self.config.templates[name]
        tpl = self.cases.get_template(domainId=self.env["domain"], templateId=entry["id"])
        return {"status": tpl.get("status"), "arquivos": [self.save(f"template-{name}.json", tpl)]}

    def opcoes(self, name):
        spec = self.config.opcoes[name]
        options, token = [], None
        while True:
            kwargs = {"domainId": self.env["domain"], "fieldId": spec["campo_id"], "maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            page = self.cases.list_field_options(**kwargs)
            options.extend(page.get("options", []))
            token = page.get("nextToken")
            if not token:
                break
        return {"total": len(options), "arquivos": [self.save(f"opcoes-{name}.json", options)]}

    def agente(self, name):
        wanted = self.config.agente["usuario"]
        found, token = None, None
        while found is None:
            kwargs = {"InstanceId": self.env["instance"], "MaxResults": 100}
            if token:
                kwargs["NextToken"] = token
            page = self.connect.list_users(**kwargs)
            found = next((u for u in page.get("UserSummaryList", []) if u.get("Username") == wanted), None)
            token = page.get("NextToken")
            if found is None and not token:
                raise ConfigError(f"usuario de teste {wanted!r} nao existe nesta instancia")
        user = self.connect.describe_user(InstanceId=self.env["instance"], UserId=found["Id"])["User"]
        identity = user.get("IdentityInfo") or {}
        data = {"id": user["Id"], "username": user.get("Username"),
                "firstName": identity.get("FirstName"), "lastName": identity.get("LastName")}
        return {"arquivos": [self.save(f"agente-{name}.json", data)]}


def run(config, env, flows, dry_run=False):
    """Download (or, with dry_run, list) the resources for these flows. Return the exit code."""
    if env["environment"] != config.ambiente:
        raise ConfigError(
            f"ambiente do arquivo ({env['environment']!r}) difere do config ({config.ambiente!r})")
    target = export_dir(config)
    items = plan(config, flows)
    if dry_run:
        print(f"Baixaria {len(items)} item(ns) para {target}, sem chamar a AWS:")
        for kind, name in items:
            print(f"  {kind:<9} {name}")
        return 0

    dl = Downloader(config, env, target)
    report = []
    for kind, name in items:
        try:
            result = dl.get(kind, name)
            report.append({"item": f"{kind}:{name}", "ok": True, **result})
            print(f"  OK     {kind:<9} {name}")
        except Exception as exc:  # recorded; the other items still run
            report.append({"item": f"{kind}:{name}", "ok": False, "erro": f"{type(exc).__name__}: {exc}"})
            print(f"  ERRO   {kind:<9} {name}: {type(exc).__name__}: {exc}")
    manifesto = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ambiente": env["environment"],
        "perfil": env["profile"],
        "fluxos": [flow.name for flow in flows],
        "itens": report,
    }
    dl.save("_manifesto.json", manifesto)
    failed = [r for r in report if not r["ok"]]
    print(f"\n{len(report) - len(failed)} de {len(report)} itens gravados em {target}")
    return 1 if failed else 0

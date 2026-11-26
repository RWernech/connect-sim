"""Command line for connect-sim: check, run, run-all, logs."""

import argparse
import json
import sys
from pathlib import Path

from . import baixar, conferir, engine, logs
from . import flow as flowmod
from . import scenario as scen
from .config import Config, ConfigError

OK_OUTCOMES = {"end_flow", "disconnected"}


def build_parser():
    parser = argparse.ArgumentParser(prog="connect-sim", description="Simula contact flows do Amazon Connect localmente.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="checa a estrutura do flow e as referencias do config")
    _flow_config_args(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", help="roda um cenario")
    _flow_config_args(p)
    p.add_argument("--scenario", required=True, help="arquivo .json do cenario")
    p.add_argument("--manter", type=int, default=20, help="execucoes guardadas por cenario (padrao: 20)")
    p.add_argument("--runs-dir", default="runs", help="onde gravar os logs (padrao: ./runs)")
    p.add_argument("--quiet", action="store_true", help="sem o passo a passo, so o resultado")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("run-all", help="roda todos os cenarios de uma pasta")
    _flow_config_args(p)
    p.add_argument("--scenarios", required=True, help="pasta com os cenarios .json")
    p.add_argument("--manter", type=int, default=20, help="execucoes guardadas por cenario (padrao: 20)")
    p.add_argument("--detalhe", action="store_true", help="mostra o passo a passo de cada cenario")
    p.add_argument("--runs-dir", default="runs", help="onde gravar os logs (padrao: ./runs)")
    p.set_defaults(func=cmd_run_all)

    p = sub.add_parser("logs", help="lista ou mostra execucoes gravadas")
    p.add_argument("--flow", required=True, help="arquivo .json do flow")
    p.add_argument("--runs-dir", default="runs", help="onde estao os logs (padrao: ./runs)")
    p.add_argument("--stream", help="nome de uma execucao para ver os passos")
    p.add_argument("--only-problems", action="store_true", help="mostra so passos com erro ou aviso")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("baixar", help="baixa da AWS, so leitura, os recursos que os flows informados usam")
    p.add_argument("--config", required=True, help="config do cliente, ex.: work/<cliente>/config/sim-stg.json")
    p.add_argument("--ambiente", required=True, help="arquivo do ambiente do cliente, ex.: work/<cliente>/config/stg.json")
    p.add_argument("--flow", action="append", required=True, help="flow sob teste; pode repetir")
    p.add_argument("--dry-run", action="store_true", help="so lista o que seria baixado; nao chama a AWS")
    p.set_defaults(func=cmd_baixar)

    p = sub.add_parser("conferir", help="confere os downloads com o config e os arquivos locais; nao chama a AWS")
    p.add_argument("--config", required=True, help="config do cliente")
    p.add_argument("--aceitar-atuais", action="store_true",
                   help="grava as divergencias atuais no arquivo de aceitas do config; so use depois de revisar")
    p.set_defaults(func=cmd_conferir)
    return parser


def _flow_config_args(p):
    p.add_argument("--flow", required=True, help="arquivo .json do flow")
    p.add_argument("--config", required=True, help="arquivo .json do config do cliente")


def _print_findings(findings):
    for f in findings:
        print(f"{f.level:<5} {f.where:<24} {f.message}")


def _load(flow_path, config_path):
    return flowmod.Flow(flow_path), Config(config_path)


def cmd_check(args):
    flow, config = _load(args.flow, args.config)
    report = flowmod.check(flow, config)
    _print_findings(report.findings)
    unused = flowmod.unused_entries(config, report.used)
    for item in unused:
        print(f"INFO  {'config':<24} {item} nao e usado pelo flow")
    errors = len(report.errors)
    warns = sum(1 for f in report.findings if f.level == "WARN")
    print(f"\n{errors} erro(s), {warns} aviso(s).")
    return 1 if errors else 0


def run_scenario(flow, config, scenario, runs_dir, echo=True, manter=None, compact=False):
    """Run one scenario, write its log, print the verdict. Return True when it passes.

    compact prints one verdict line, plus the divergences and the failing step. The
    step-by-step trace is still written to the log folder.
    """
    log = logs.RunLog(runs_dir, flow.name, scenario.name, echo=echo)
    if echo:
        print(f"Cenario: {scenario.name}. {scenario.description}".rstrip())
    result = engine.Engine(flow, config, scenario, log.event).run()
    problems = scen.mismatches(result, scenario.expect) if scenario.expect else []
    expected_divergence = scenario.expect_divergence
    if expected_divergence and not scenario.expect:
        problems = ["expect_divergence exige um bloco expect"]
        passed = False
    elif expected_divergence:
        passed = bool(problems)
    elif scenario.expect:
        passed = not problems
    else:
        passed = result.outcome in OK_OUTCOMES
    verdict = "PASSOU" if passed else "FALHOU"
    summary = {
        "flow": flow.name,
        "scenario": scenario.name,
        "verdict": verdict,
        "passed": passed,
        "outcome": result.outcome,
        "failed_at": result.failed_at,
        "reason": result.reason,
        "path": result.path,
        "attributes": result.attributes,
        "cases": result.cases,
        "expect_divergence": expected_divergence,
        "mismatches": problems,
    }
    log.close(summary)
    logs.prune(runs_dir, flow.name, scenario.name, manter)
    if compact:
        print(f"{verdict:<8} {scenario.name} ({result.outcome})")
        if not passed and result.failed_at:
            print(f"         parou em {result.failed_at}: {result.reason}")
        for problem in problems:
            print(f"         {'DIVERGE (esperada)' if expected_divergence and passed else 'DIVERGE'}: {problem}")
        return passed
    print(f"\n{scenario.name}: {verdict} ({result.outcome})")
    if result.failed_at:
        print(f"parou em {result.failed_at}: {result.reason}")
    if expected_divergence and not problems:
        print("divergencia esperada nao apareceu: o problema conhecido nao reproduziu")
    label = "DIVERGE (esperada)" if expected_divergence and passed else "DIVERGE"
    for problem in problems:
        print(f"{label}: {problem}")
    print(f"log: {log.dir}")
    return passed


def load_prepared(path, config):
    """Load a scenario and resolve its view actions against the config. Return (scenario, problems)."""
    scenario = scen.load_scenario(path)
    return scenario, scen.resolve_views(scenario, config)


def cmd_run(args):
    flow, config = _load(args.flow, args.config)
    report = flowmod.check(flow, config)
    if report.errors:
        _print_findings(report.findings)
        print("\nO check achou erros; corrija antes de simular.")
        return 1
    scenario, problems = load_prepared(args.scenario, config)
    if problems:
        for problem in problems:
            print(f"ERRO cenario: {problem}")
        return 1
    return 0 if run_scenario(flow, config, scenario, args.runs_dir, echo=not args.quiet, manter=args.manter) else 1


def cmd_run_all(args):
    flow, config = _load(args.flow, args.config)
    report = flowmod.check(flow, config)
    if report.errors:
        _print_findings(report.findings)
        print("\nO check achou erros; corrija antes de simular.")
        return 1
    files = sorted(Path(args.scenarios).glob("*.json"))
    if not files:
        print(f"nenhum cenario .json em {args.scenarios}")
        return 2
    results = []
    for path in files:
        scenario, problems = load_prepared(path, config)
        if problems:
            for problem in problems:
                print(f"ERRO {scenario.name}: {problem}")
            results.append((scenario.name, False))
            continue
        results.append((scenario.name, run_scenario(
            flow, config, scenario, args.runs_dir, echo=args.detalhe, manter=args.manter, compact=not args.detalhe)))
    passed_count = sum(1 for _name, passed in results if passed)
    print(f"\n{passed_count} de {len(results)} cenario(s) passaram.")
    return 0 if passed_count == len(results) else 1


def console():
    """Entry point for 'uv run connect-sim ...' (see [project.scripts] in pyproject.toml)."""
    sys.exit(main())


def cmd_logs(args):
    flow_name = Path(args.flow).stem
    if args.stream:
        stream_dir = Path(args.runs_dir) / logs.GROUP_ROOT / flow_name / args.stream
        if not (stream_dir / "summary.json").is_file():
            print(f"execucao nao encontrada: {stream_dir}")
            return 1
        summary = logs.read_summary(stream_dir)
        print(f"{args.stream}: {summary['verdict']} ({summary['outcome']})")
        for event in logs.read_events(stream_dir, only_problems=args.only_problems):
            print(logs.format_event(event))
        return 0
    streams = logs.list_streams(args.runs_dir, flow_name)
    if not streams:
        print("nenhuma execucao gravada para este flow")
        return 0
    for stream in streams:
        summary = logs.read_summary(stream)
        print(f"{stream.name:<60} {summary['verdict']:<8} {summary['outcome']}")
    return 0


def cmd_baixar(args):
    config = Config(args.config)
    env = baixar.load_ambiente(args.ambiente)
    flows = [flowmod.Flow(path) for path in args.flow]
    return baixar.run(config, env, flows, dry_run=args.dry_run)


def cmd_conferir(args):
    config = Config(args.config)
    lines, falhas = conferir.conferir(config)
    if args.aceitar_atuais:
        conferir.gravar_aceitas(config, falhas)
    aceitas = config.divergencias_aceitas()
    for line in lines:
        key = line.strip()
        tag = " [aceita]" if key in falhas and key in aceitas else ""
        print(line + tag)
    distintas = set(falhas)
    novas = distintas - aceitas
    print(f"\n{len(novas)} divergencia(s) nova(s), {len(distintas) - len(novas)} aceita(s).")
    return 1 if novas else 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, json.JSONDecodeError, ConfigError, KeyError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

# Atalho do simulador. Uso, de qualquer pasta:
#   <caminho>\tools\connect-sim\connect-sim.ps1 baixar --config ... --ambiente ... --flow ... --dry-run
#   <caminho>\tools\connect-sim\connect-sim.ps1 run-all --flow ... --config ... --scenarios ...
#
# Roda com o extra 'aws' (boto3), que so o 'baixar' usa. Os outros comandos funcionam igual.
# Caminhos passados como argumento continuam relativos a pasta onde voce esta.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = $here
uv run --project $here --python 3.11 --extra aws python -m connect_sim @args
exit $LASTEXITCODE

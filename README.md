# connect-sim

Roda flows do Amazon Connect na sua máquina, passo a passo, antes de subir para a AWS.
Ele mostra qual bloco falhou, o que cada bloco fez e se o resultado bate com o esperado.
Não fala com a AWS, então não gasta recurso nem toca em QA ou STG.

## O que ele cobre

Ramos por condição e por erro, atributos de contato, `$.External` vindo de Lambda, `$.Views.ViewResultData` vindo de view, criação de caso com validação dos campos do template, fila alvo, transfer, desconexão e encerramento.

O simulador não reproduz áudio, prompts, Lex com NLU real, filas e agentes reais, cotas, nem o código publicado na Lambda. Quando o flow usa um bloco sem simulador, a execução para com `NotSimulated`. Ele não assume sucesso.

## Requisitos

Python 3.11. Use o `uv`, que baixa o Python 3.11 se ele não estiver instalado. O pacote não tem dependências externas.

## Comandos

Rode os comandos a partir da pasta `tools/connect-sim`.

Checagem estática do flow, com as referências resolvidas no config:

    uv run --python 3.11 -m connect_sim check --flow examples/flows/contato-referencia.json --config examples/config/generico.json

Um cenário:

    uv run --python 3.11 -m connect_sim run --flow examples/flows/contato-referencia.json --config examples/config/generico.json --scenario examples/scenarios/caminho-feliz.json

Todos os cenários de uma pasta:

    uv run --python 3.11 -m connect_sim run-all --flow examples/flows/contato-referencia.json --config examples/config/generico.json --scenarios examples/scenarios

Logs de execuções anteriores:

    uv run --python 3.11 -m connect_sim logs --flow examples/flows/contato-referencia.json
    uv run --python 3.11 -m connect_sim logs --flow examples/flows/contato-referencia.json --stream caso-sem-vinculo-<carimbo> --only-problems

O `check` sai com código 1 quando acha erro. O `run` e o `run-all` saem com código 1 quando um cenário falha, o que serve para CI.

## Como ler o passo a passo

Cada linha é um bloco executado, na ordem:

    #07 ERROR Abrir_caso    CreateCase    ContactNotLinked: caso case-0001 criado, contato nao vinculado  -> Definir_fila
         WARN  campo de sistema 'created_by' ficou vazio; o fluxo nao preenche

`ERROR` é um erro do Connect que foi tratado por um ramo do flow. Quando o erro não tem ramo, a execução para com `outcome` igual a `unhandled_error` e o campo `failed_at` aponta o bloco.
`WARN` é um aviso que não para o fluxo.
`-> Definir_fila` mostra para qual bloco o fluxo seguiu.

## Logs

Cada execução grava em `runs/connect-sim/<flow>/<cenario>-<carimbo UTC>/`. O grupo e o stream seguem o nome do CloudWatch. O arquivo `events.jsonl` tem um registro por bloco. O `summary.json` tem o veredito, o caminho percorrido, os atributos finais e os casos criados. A pasta `runs/` fica fora do git.

## Cenário

Um cenário é um JSON com o que o cliente responde (`inputs`), o que o agente responde na view (`views`), o estado das filas (`queues`), o retorno de Lambda ou uma falha simulada (`lambdas`), e o resultado esperado (`expect`). O `expect` pode checar `outcome`, `path` (lista exata de blocos), `failed_at` e `attributes`. Um cenário sem `expect` passa quando o flow termina em `end_flow` ou `disconnected`.

Valores de `outcome`: `end_flow` (EndFlowExecution), `disconnected` (DisconnectParticipant), `unhandled_error`, `config_error` e `loop_limit`.

## Config por cliente

O flow nunca traz IDs reais. Ele usa placeholders como `{{queue.atendimento.id}}`, `{{view.triagem.id}}`, `{{template.caso_padrao.id}}`, `{{field.caso_padrao.motivo}}` e `{{lambda.lookup_cliente.arn}}`. O arquivo de config dá o valor de cada um. Por isso o mesmo flow genérico serve a vários clientes.

Para um cliente novo, copie `examples/config/generico.json` para `work/<cliente>/config/` e troque os IDs. O campo `local` de cada Lambda aponta para o código Python que o simulador roda no lugar da função publicada. Use um cenário com `"lambdas": {"nome": {"response": {...}}}` quando não quiser rodar o código.

O `check` mostra os campos de sistema do template que o flow não preenche, como `created_by`. Também lista as entradas do config que o flow não usa.

## Regras que o check aplica

O `check` valida a estrutura do JSON: Identifier, Type, Transitions, NextAction e operadores. Também valida as regras que vieram das páginas de cada Action na documentação oficial: erros aceitos por Action, faixa de `InvocationTimeLimitSeconds` (1 a 8), ramo `NoMatchingError` obrigatório no GetParticipantInput, e Lambda sem Conditions.

Tipos de Action sem regra verificada recebem só a checagem estrutural. Para ampliar, consulte a página da Action com `get_action_doc` e acrescente a regra em `connect_sim/flow.py`.

## Estrutura

    connect_sim/
      cli.py        comandos
      flow.py       checagem estática
      engine.py     execução passo a passo
      config.py     placeholders e config do cliente
      resources.py  casos e carregamento de Lambda local
      scenario.py   cenários e expectativas
      logs.py       log local no formato de grupo e stream
    examples/
      flows/        flow genérico de referência
      config/       config de exemplo com IDs fictícios
      lambdas/      Lambda local de exemplo
      scenarios/    cenários de exemplo

## Compartilhar

A pasta inteira vai junto: código, exemplos e este README. Não há instalação. Quem tiver o `uv` roda os comandos acima. Os IDs de exemplo são fictícios e os de um cliente real ficam só em `work/<cliente>/config/`, fora deste pacote.

## Cenários, config e checagens

- Cenário com `"expect_divergence": true` passa quando a execução diverge do bloco `expect`. Sem divergência, o cenário falha, porque o problema conhecido deixou de reproduzir. Sem bloco `expect`, o cenário falha com erro de configuração.
- Ação de view em cenário e em flow pode usar placeholder, como `{{view.triagem.acao_enviar}}`. A config lista as ações da view em `acao_*` (ou na lista `actions`). Ação que a view não responde falha antes da execução.
- Condição de ShowView é resolvida pela config, como os parâmetros de cada bloco.
- `opcoes` aponta para um arquivo local com as opções de um campo SingleSelect. O `check` acusa qualquer Compare sobre o atributo configurado que teste um valor fora das opções ativas, inclusive em módulo chamado por `InvokeFlowModule` com `{{module.<nome>}}`.
- Nenhum comando do simulador chama a AWS. Os recursos são baixados antes, com um script separado.

## Baixar e conferir (AWS)

- `connect-sim baixar --config <sim-config> --ambiente <ambiente.json> --flow <flow.json> [--flow ...] [--dry-run]` baixa só o que os flows informados usam: views, módulos e templates que eles alcançam, os catálogos de opções do config e o agente de teste. Só faz chamadas de leitura (get, list, describe).
- Conta, perfil, região, instância e domínio vêm do arquivo de ambiente do cliente, o mesmo que a esteira lê (ex.: `work/<cliente>/config/stg.json`). O config do simulador traz só os IDs dos recursos.
- Os arquivos vão para a pasta `exportacao` do config, que precisa ficar dentro de `work/<cliente>/`. Config fora dessa pasta para o comando antes de qualquer chamada à AWS.
- `--dry-run` lista o que seria baixado e não chama a AWS.
- `connect-sim conferir --config <sim-config>` compara o export com o config e com os arquivos locais. Não chama a AWS. Sai com 1 quando há divergência.

O `baixar` usa boto3, que fica no extra `aws` do `pyproject.toml`. Rode assim, a partir de `tools/connect-sim`:

    uv run --python 3.11 --extra aws -m connect_sim baixar --config ..\..\work\<cliente>\config\<sim>.json --ambiente ..\..\work\<cliente>\config\<ambiente>.json --flow ..\..\work\<cliente>\02-flows\<fluxo>.json

Os demais comandos (check, run, run-all, conferir) não precisam do extra.

## Testes e divergências aceitas

- Testes unitários, offline, sem AWS: `uv run --python 3.11 --extra test python -m pytest tests -q`. Cada teste monta o próprio cliente temporário, com IDs inventados.
- Divergências já revisadas ficam no arquivo do config indicado em `divergencias_aceitas`. O `conferir` mostra essas linhas com `[aceita]` e só falha por divergência nova.
- `connect-sim conferir --config <sim-config> --aceitar-atuais` grava as divergências atuais no arquivo de aceitas. Use só depois de revisar a lista. Uma divergência que some do export continua no arquivo, então rever de tempos em tempos.

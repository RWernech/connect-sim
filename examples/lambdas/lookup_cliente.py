"""Local stand-in for the lookup_cliente Lambda.

Replace the body with the deployed logic when the real code is available, or
mock the call inside a scenario with "lambdas": {"lookup_cliente": {"response": {...}}}.
"""

FAKE_CUSTOMERS = {
    "000000000001": {"nome": "Cliente Exemplo", "status": "ativo"},
}


def handler(event, context):
    client_id = event["Details"]["Parameters"].get("ClienteId", "")
    found = FAKE_CUSTOMERS.get(client_id)
    if found is None:
        return {"nome": "", "status": "nao_encontrado"}
    return dict(found)

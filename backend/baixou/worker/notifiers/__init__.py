"""Notificadores do worker: contrato comum + Telegram (ntfy chega no 03-02).

Interface pequena e comum da arquitetura (§Interfaces de fontes e
notificadores): cada canal é um módulo que envia uma mensagem com título
(sempre) e devolve sucesso/falha SEM lançar exceção (``NotificationResult``
com kind estável em EN) — a regra "o aviso conta como enviado se pelo menos
um canal aceitar" fica no worker (worker.alerts), nunca nos notificadores.
Só stdlib + httpx aqui: fastapi/pydantic são proibidos no worker (D-07,
ruff TID251).
"""

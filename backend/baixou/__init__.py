"""Baixou — alerta de preço self-hosted para ações da B3 e criptomoedas.

Pacote único com submódulos core (stdlib + httpx), web (FastAPI) e worker
(laço puro). O worker e o core nunca importam fastapi/pydantic (D-07).
"""

"""Fontes de cotação do worker: contrato comum + brapi (B3) + Binance (cripto).

Interface pequena e comum da arquitetura (§Interfaces de fontes, FR-05): cada
fonte é um módulo que consulta seu mercado e devolve resultados tipados
(``QuoteResult``) ou falha com erro tipado (``SourceError`` com ``kind``
estável em EN) — o worker decide pelo tipo, nunca pelo texto. Só stdlib +
httpx aqui: fastapi/pydantic são proibidos no worker (D-07, ruff TID251).
"""

# Política de segurança

## Versões suportadas

| Versão | Suportada |
| ------ | --------- |
| 0.1    | Sim       |

O Baixou está na v0.1: correções de segurança acompanham a linha `main` e
chegam na próxima imagem publicada no registro de contêineres. Não há
backport para versões antigas — atualize com o `pull` do compose.

## Como reportar uma vulnerabilidade

Reporte **em privado** pelo recurso de advisory de segurança do GitHub:
aba **Security** do repositório → **Report a vulnerability**.

**Não abra issue pública** para vulnerabilidade — descrever a falha em
público expõe quem já roda a versão afetada antes de existir correção.

No relatório, inclua:

- a versão/tag da imagem que você roda;
- os passos para reproduzir (o que fez, o que esperava, o que aconteceu);
- logs se ajudarem — **remova tokens, senhas e identificadores antes de
  colar** (veja abaixo o que nunca deve sair da sua instalação).

**Prazo de resposta:** este é um projeto pessoal mantido por melhor esforço —
espere resposta em dias, não em horas. Confirmada a falha, a correção sai
como versão nova e o advisory é publicado quando a imagem corrigida já
estiver disponível para atualização.

## Este projeto é para rede doméstica

O Baixou foi feito para rodar em **rede doméstica, atrás de um proxy HTTPS**
(Caddy ou equivalente). **Não exponha o serviço diretamente na internet.**

- O compose de instalação publica a porta **somente em `127.0.0.1`** —
  mantenha assim e deixe o proxy na frente, cuidando de TLS e do acesso;
- publicar a porta direto (ou usar `0.0.0.0`) pula a camada de rede pensada
  para o projeto e transforma sua instância em alvo de varredura pública;
- lembre-se: em canais públicos como o ntfy.sh, **o nome do tópico funciona
  como senha** — use um tópico difícil de adivinhar e nunca o publique.

## O que NÃO é escopo de segurança

- **Dados de mercado com atraso ou imprecisos** — as fontes são gratuitas e
  o README traz o aviso de que o projeto não é recomendação de investimento;
- **exatidão das cotações** — preço errado ou atrasado da fonte não é falha
  de segurança do Baixou;
- a segurança do **seu** servidor, do seu proxy, do seu bot do Telegram ou do
  seu ntfy próprio — cada pessoa usa as próprias chaves e é responsável pela
  própria infraestrutura.

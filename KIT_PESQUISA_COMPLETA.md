# 🔬 Kit de Pesquisa de Mercado Completa — ML Analista

Fluxo em 3 blocos: **API** (automático) → **Avantpro + Shopping** (prompts prontos)
→ **Dashboard**. Objetivo: reunir TODO dado que ajuda a decidir entrar num produto —
volume de vendas, 30 dias, dados do produto, concorrentes e preço de mercado.

---

## 🟦 BLOCO 1 — API do Mercado Livre (rode primeiro, no Claude com MCP)

Rode **uma por vez**:

| Comando | Traz |
|---|---|
| `pesquisar_concorrentes("<termo>")` | Catálogos campeões, categoria prevista, ficha do líder |
| `analisar_concorrentes("<termo>", ticket_medio=<preço>)` | Vendedores fortes, reputação, transações |
| `anuncio_completo("MLBxxxx")` | Preço, estoque, fotos, catálogo, reviews de UM anúncio |
| `anuncio_visitas("MLBxxxx", dias=30)` | Visitas/dia dos últimos 30 dias (tráfego) |
| `anuncio_buybox("MLBxxxx")` | Situação no catálogo e preço para ganhar o buybox |

**O que a API entrega:** dados do produto, concorrentes, categoria, visitas, buybox.
**O que a API NÃO entrega** (→ vem do Avantpro): volume de vendas real, faturamento.

Anote os catálogos (MLBxxxx) dos 2-3 concorrentes mais fortes — eles vão para o Bloco 2.

---

## 🟩 BLOCO 2 — Avantpro (volume de vendas real)

Abra no Chrome (Claude in Chrome ativo) a **página de cada concorrente forte** com o
Avantpro habilitado. Cole este prompt:

```
Você está numa página de produto do Mercado Livre com a extensão Avantpro ativa.
Leia a tela (get_page_text + screenshot) e devolva SÓ estes dados, em lista.
Se algum não estiver visível, escreva "não visível" — NÃO invente número.

DADOS DO PRODUTO
- Nome:
- Catálogo (MLBxxxx):
- Marca:
- Preço atual (buybox):
- Preço "de" / desconto:
- Parcelamento:
- Frete grátis? Full?
- Avaliação (estrelas) e nº de reviews:

VENDEDOR
- Vendedor ganhando o buybox:
- Nº de vendedores no anúncio:
- Reputação do vendedor (verde/amarelo/vermelho):

AVANTPRO — VOLUME (o principal)
- Vendas nas últimas 24h / por dia:
- Vendas nos últimos 7 dias:
- Vendas nos últimos 30 dias:
- Faturamento estimado 30 dias:
- Ritmo atual (unidades/dia):
- Tendência (subindo / estável / caindo):
- Ranking na categoria (se mostrar):
- Data de criação do anúncio (idade):
```

Repita para 2-3 concorrentes. Cole todas as respostas no Claude do dashboard.

---

## 🟨 BLOCO 3 — Shopping de Preços (preço de mercado fora do ML)

Abra Google Shopping (ou Buscapé / Zoom), busque o produto e cole este prompt:

```
Você está numa busca de shopping de preços (Google Shopping/Buscapé/Zoom).
Leia a tela e devolva SÓ estes dados, em lista. Não invente:

- Produto buscado:
- Menor preço encontrado + loja:
- Maior preço encontrado + loja:
- Preço médio de mercado:
- Nº de lojas/ofertas listadas:
- O preço do Mercado Livre está acima ou abaixo do mercado?
```

---

## 🟪 BLOCO 4 — Consolidação → Dashboard

Cole no Claude (com dashboard) TUDO que os Blocos 1-3 geraram + informe:
- **Custo do fornecedor** (para calcular margem)
- **Preço que você pretende vender**

O Claude monta um **dashboard esmiuçado** com:
- Identificação do produto + categoria
- Concorrentes (título, preço, vendedor, força) — Bloco 1
- **Volume de vendas 30 dias** por concorrente — Bloco 2
- Faixa de preço de mercado — Bloco 3
- Margem estimada e break-even ROAS
- **Veredicto: entrar / não entrar / testar** + o porquê
- Fontes usadas e hora da consulta

---

## ✅ Checklist "todo dado bom"
- [ ] Concorrentes e líder (API)
- [ ] Ficha técnica do líder (API)
- [ ] Visitas 30 dias (API)
- [ ] Situação no buybox (API)
- [ ] **Volume de vendas 30 dias** (Avantpro) ← o mais importante
- [ ] Faturamento estimado (Avantpro)
- [ ] Ritmo e tendência (Avantpro)
- [ ] Reputação dos vendedores (Avantpro/API)
- [ ] Faixa de preço de mercado (Shopping)
- [ ] Custo do fornecedor + preço-alvo (você) → margem

---

## ⚠️ Regras anti-erro
1. **Uma chamada de API por vez** (rajada = bloqueio 403).
2. **Se aparecer 🔌 (reconectar)** → token expirou, reconecte no site e refaça.
3. **Nunca inventar número** — se uma fonte falhar, marque "não coletado" e siga.
4. **Volume só vem do Avantpro** (a API não expõe). Sempre confirme na tela.

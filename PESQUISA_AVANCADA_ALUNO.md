# 🔎 Pesquisa de Mercado Avançada — Guia do Aluno (ML Analista)

> Cole este guia no seu Claude (Desktop ou Chrome) e depois faça seu pedido.
> Ele orquestra API + navegador + Avantpro + shopping de preços e entrega um
> dashboard completo. É a mesma metodologia usada na operação NEOSHOP.

---

## 🎯 O que este fluxo faz
Você pede uma pesquisa de um produto ou categoria. O Claude:
1. Busca o que dá na **API do Mercado Livre** (rápido, dados reais).
2. Decide se é suficiente. Se não for, **abre o navegador** e coleta o resto.
3. Monta um **dashboard esmiuçado** com concorrentes, preços e volume estimado.

---

## 🛠️ Ferramentas que o Claude vai usar
- **MCP ML Analista** (sua conexão na nuvem) — dados da API do ML
- **Claude in Chrome** — para ler telas que a API não entrega
- **Avantpro** (extensão) — vendas estimadas por anúncio
- **Shopping de preços** (Google Shopping / Buscapé / Zoom) — faixa de preço real

---

## 📋 INSTRUÇÕES PARA O CLAUDE (siga exatamente nesta ordem)

### FASE 1 — API primeiro (sempre)
Rode, nesta ordem, **uma por vez** (nunca em paralelo, evita bloqueio):
1. `pesquisar_concorrentes("<termo do aluno>")` → títulos campeões, categoria, ficha do líder
2. `analisar_concorrentes("<termo>", ticket_medio=<preço estimado>)` → vendedores e força

**Regra de ouro:** se qualquer tool responder com `erro_conexao` (🔌 reconectar),
PARE e avise o aluno para reconectar a conta no site. Não continue com dado velho.

### FASE 2 — Decisão: os dados bastam?
Os dados da API **NÃO bastam** quando o aluno precisa de:
- **Volume de vendas real** (a API não expõe — só dá proxy)
- **Ranking de "mais vendidos"** da categoria
- **Faixa de preço** fora do ML (mercado geral)

Se bastar → pule para a FASE 4 (dashboard).
Se não bastar → siga para a FASE 3.

### FASE 3 — Navegador (Claude in Chrome)
Antes de abrir o navegador, **confirme com o aluno**:
> "Para completar preciso confirmar: **qual produto exato** e **qual categoria**?"

Depois colete, uma fonte por vez:

**3a. Mais vendidos da categoria (Mercado Livre)**
- ⚠️ NÃO use `lista.mercadolivre.com.br` direto — o grid não carrega (SPA/anti-bot).
- Em vez disso: peça ao aluno para **abrir a categoria no ML já logado** e ordene por
  "Mais relevantes / Mais vendidos"; então leia a tela com `get_page_text`.
- Alternativa: use `pesquisar_concorrentes` da API (já traz os líderes do catálogo).

**3b. Avantpro (vendas estimadas)**
- Abra a **página do produto/anúncio** concorrente no ML.
- O overlay do Avantpro mostra vendas estimadas e ritmo. Leia com `screenshot` +
  `get_page_text`. Se o overlay não aparecer, peça ao aluno para ativar a extensão.

**3c. Shopping de preços (faixa de preço real)**
- Abra Google Shopping (ou Buscapé/Zoom), busque o produto e leia a faixa de preço.
- Anote menor, maior e preço médio de mercado.

### FASE 4 — Dashboard esmiuçado
Monte um relatório visual (use show_widget / tabela) com:
- **Identificação** do produto + categoria
- **Concorrentes** (título, preço, vendedor, força) — da API
- **Volume estimado** (Avantpro) — vendas/mês por concorrente
- **Faixa de preço de mercado** (shopping de preços) — menor / médio / maior
- **Margem estimada** se o aluno informar custo do fornecedor
- **Veredicto**: entrar / não entrar / testar, com o porquê
- **Fontes usadas** e a hora da consulta (transparência)

---

## ⚠️ Regras que evitam erro
1. **Uma chamada de API por vez** (nunca em rajada) — o ML bloqueia simultâneas.
2. **Se aparecer 🔌 (reconectar)** → o problema é o token do aluno, não a pesquisa.
   Ele reconecta no site e refaz o pedido.
3. **Volume de vendas exato não existe na API** — sempre vem do Avantpro (tela).
4. **Nunca invente número.** Se uma fonte falhar, diga o que faltou e siga com o resto.

---

## 🚀 Prompt de ativação (o aluno cola isto depois do guia)
> "Faça uma pesquisa de mercado completa de **[PRODUTO]** na categoria **[CATEGORIA]**.
> Comece pela API, e se faltar volume ou preço, use o Chrome com Avantpro e shopping
> de preços. No fim me entregue o dashboard esmiuçado."

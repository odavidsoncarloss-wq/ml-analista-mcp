"""
ML ANALISTA — MCP Beta v1.0 (NEOSHOP / @odavidson.ia)
Servidor MCP que conecta o Claude do aluno à conta Mercado Livre DELE.
100% API oficial ML — sem scraping, sem senha, só o token OAuth do próprio aluno.

Uso: configurado automaticamente pelo instalar.bat
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests

SCRIPT_DIR = Path(__file__).parent
# Procura config.json na mesma pasta (ml-coleta-aluno)
CONFIG_FILE = SCRIPT_DIR / "config.json"
ENV_FILE   = SCRIPT_DIR / ".env"

# MCP server real (FastMCP). Fallback para Mock se o SDK nao estiver instalado
# (permite importar o arquivo sem o MCP, ex.: em testes ou na coleta agendada).
try:
    from mcp.server.fastmcp import FastMCP
    # Desliga a protecao de Host (anti-DNS-rebinding) do FastMCP: como o
    # servidor roda atras do nginx (HTTPS) e a auth e' feita pela ?key=, o
    # Host chega como mcp.iacomdavidson.com.br e seria rejeitado (HTTP 421).
    # stateless_http=True: cada requisicao HTTP e' independente, entao o
    # contextvar do inquilino (setado pelo middleware com a ?key=) chega na
    # execucao da ferramenta. No modo stateful a tool roda em outra task e o
    # contexto se perde (caia no config.json local inexistente).
    try:
        from mcp.server.transport_security import TransportSecuritySettings
        _sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        mcp = FastMCP("ML Analista", transport_security=_sec, stateless_http=True)
    except Exception:
        mcp = FastMCP("ML Analista", stateless_http=True)
except ImportError:
    class MockMCP:
        def tool(self):
            def decorator(func):
                return func
            return decorator
    mcp = MockMCP()

PERIODOS = {"hoje": 0, "ontem": 1, "semanal": 6, "quinzenal": 14, "mensal": 29}


# ── Auth ────────────────────────────────────────────────────────────────────

# Multi-inquilino: quando rodando como servidor HTTP, cada requisição define
# qual config usar (do aluno). Pode ser:
#   - None  -> modo local (Claude Desktop / coleta): usa CONFIG_FILE da NEOSHOP
#   - Path  -> arquivo config.json de um aluno (tenants por arquivo)
#   - dict  -> config em memoria (vindo do Supabase), opcional "_save" callback
from contextvars import ContextVar
_tenant_config: "ContextVar" = ContextVar("tenant_config", default=None)

def _key_do_request_mcp():
    """Le a ?key= do request HTTP atual (streamable-http), de DENTRO da tool.
    O SDK seta request_ctx na mesma task que roda a ferramenta, entao aqui
    conseguimos o request HTTP (Starlette) e a query string com a chave."""
    try:
        from mcp.server.lowlevel.server import request_ctx
        rc = request_ctx.get(None)
        if rc is None:
            return None
        req = getattr(rc, "request", None)
        qp = getattr(req, "query_params", None)
        if qp is not None:
            return qp.get("key")
    except Exception:
        pass
    return None


def _load_tenant_config():
    """Devolve (config_dict, save_fn) do inquilino atual ou do local."""
    src = _tenant_config.get()
    if src is None:
        # streamable-http: pega a chave do request MCP atual e resolve o aluno
        _key = _key_do_request_mcp()
        if _key:
            try:
                import tenant_store
                src = tenant_store.resolve(_key)
            except Exception:
                src = None
    if src is None:
        src = CONFIG_FILE
    # Config em memoria (Supabase)
    if isinstance(src, dict):
        saver = src.get("_save")
        def save_fn(c):
            if saver:
                saver(c)
        return src, save_fn
    # Config em arquivo (local ou tenant por arquivo)
    if not Path(src).exists():
        raise RuntimeError(f"config nao encontrado: {src}")
    cfg = json.load(open(src, encoding="utf-8"))
    def save_fn(c):
        json.dump(c, open(src, "w"), indent=2)
    return cfg, save_fn


def _env():
    env = {}
    if ENV_FILE.exists():
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _token():
    """Lê token do ML, renovando automaticamente se expirou.
    Usa o config do inquilino atual (multi-aluno em HTTP) ou o local."""
    config, _save = _load_tenant_config()
    token = config.get("access_token")
    refresh = config.get("refresh_token")
    # config.json local usa "token_expira_em"; o config vindo do Lovable usa
    # "expires_at". Checar os dois pra a renovacao automatica funcionar nos 2.
    expires = config.get("token_expira_em") or config.get("expires_at")

    if not token:
        raise RuntimeError("access_token não encontrado!")

    # Verificar se expirou
    if expires:
        from datetime import datetime
        exp_time = datetime.fromisoformat(expires.replace('Z', '+00:00'))
        if datetime.now(exp_time.tzinfo) > exp_time:
            print("[OK] Token expirado, renovando...")
            if refresh:
                try:
                    # Renovar token
                    r = requests.post(
                        "https://api.mercadolibre.com/oauth/token",
                        data={
                            "grant_type": "refresh_token",
                            "client_id": "1481015569247774",
                            "client_secret": "TNM7sQvPR9RAghPnxWuQ0UtHROKIhP9P",
                            "refresh_token": refresh
                        },
                        timeout=10
                    )
                    if r.status_code == 200:
                        data = r.json()
                        config["access_token"] = data["access_token"]
                        config["refresh_token"] = data.get("refresh_token", refresh)
                        # grava a validade como TIMESTAMP futuro (nao segundos)
                        _seg = data.get("expires_in", 21600)
                        _val = (datetime.now() + timedelta(seconds=_seg)).isoformat()
                        config["token_expira_em"] = _val
                        config["expires_at"] = _val
                        _save(config)  # persiste: arquivo (local) ou nuvem
                        token = config["access_token"]
                        token = config["access_token"]
                        print("[OK] Token renovado!")
                except:
                    print("[AVISO] Falha ao renovar, usando token atual...")

    return token


def _get(path, params=None, api_version=None, _retry=2):
    h = {"Authorization": f"Bearer {_token()}"}
    if api_version:
        h["Api-Version"] = api_version
    for tentativa in range(_retry + 1):
        r = requests.get(f"https://api.mercadolibre.com{path}", headers=h,
                         params=params or {}, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 403) and tentativa < _retry:
            # Rate limit ou bloqueio temporário — aguarda e tenta de novo
            wait = 2 ** (tentativa + 1)  # 2s, 4s
            time.sleep(wait)
            continue
        return {"_erro": r.status_code, "_msg": r.text[:300]}
    return {"_erro": 503, "_msg": "Esgotadas as tentativas após rate limit"}


def _put(path, body, api_version=None):
    h = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    if api_version:
        h["Api-Version"] = api_version
    r = requests.put(f"https://api.mercadolibre.com{path}", headers=h,
                     json=body, timeout=30)
    if r.status_code not in (200, 201):
        return {"_erro": r.status_code, "_msg": r.text[:300]}
    return r.json()


def _checar_modo_operacao():
    # Desabilitado: licenca.py removido
    # if not licenca.modo_operacao():
    #     raise RuntimeError("🔒 Recurso do MODO OPERAÇÃO (plano Avançado). "
    #                        "Fale com @odavidson.ia para fazer upgrade.")
    pass


def _advertiser_id():
    # Usa o config do inquilino atual (nuvem: vem do Lovable ja com advertiser_id;
    # local: config.json). NAO abre CONFIG_FILE direto (quebrava na nuvem).
    config, _save = _load_tenant_config()

    # 1) Se ja veio no config (Lovable manda advertiser_id), usa
    adv = config.get("advertiser_id")
    if adv:
        return str(adv)

    # 2) Fallback: busca na API de Ads
    try:
        r = _get("/advertising/advertisers", {"product_id": "PADS"}, api_version="1")
        advs = r.get("advertisers", [])
        if advs:
            return str(advs[0]["advertiser_id"])
    except:
        pass

    raise RuntimeError("Nao foi possivel encontrar advertiser_id")


def _datas(periodo):
    hoje = datetime.now().date()
    ontem = hoje - timedelta(days=1)
    if periodo == "hoje":
        return str(hoje), str(hoje)
    if periodo == "ontem":
        return str(ontem), str(ontem)
    if periodo in ("mes_atual", "mes_vigente", "mês atual"):
        # termina ontem — hoje ainda incompleto
        inicio = hoje.replace(day=1)
        return str(inicio), str(ontem)
    # periodo com datas livres: "2026-06-01:2026-06-15"
    if ":" in str(periodo):
        partes = periodo.split(":")
        if len(partes) == 2:
            return partes[0].strip(), partes[1].strip()
    # periodos rolantes terminam ontem (dia de hoje incompleto)
    dias = PERIODOS.get(periodo, 6)
    return str(ontem - timedelta(days=dias - 1)), str(ontem)


def _seller_id():
    config, _ = _load_tenant_config()
    sid = config.get("seller_id")
    if sid:
        return str(sid)
    me = _get("/users/me")
    if me.get("_erro"):
        raise RuntimeError("Não foi possível obter seller_id")
    return str(me["id"])


def _buscar_pedidos(di: str, df: str):
    """Busca pedidos pagos reais via /orders/search. Retorna (receita_total, unidades_total)."""
    try:
        seller = _seller_id()
    except Exception as e:
        return None, str(e)

    receita = 0.0
    unidades = 0
    offset = 0

    while True:
        params = {
            "seller": seller,
            "order.date_created.from": f"{di}T00:00:00.000-0300",
            "order.date_created.to": f"{df}T23:59:59.000-0300",
            "order.status": "paid",
            "limit": 50,
            "offset": offset,
        }
        r = _get("/orders/search", params)
        if r.get("_erro"):
            return None, f"Erro API pedidos: {r.get('_msg', '')}"

        resultados = r.get("results", [])
        total_pag = int((r.get("paging") or {}).get("total") or 0)

        for order in resultados:
            receita += float(order.get("total_amount") or 0)
            for item in (order.get("order_items") or []):
                unidades += int(item.get("quantity") or 0)

        offset += len(resultados)
        if not resultados or offset >= total_pag:
            break

    return {"receita": round(receita, 2), "unidades": unidades}, None


METRICAS = ("clicks,prints,cost,cpc,acos,units_quantity,total_amount,"
            "direct_units_quantity,indirect_units_quantity,direct_amount,"
            "indirect_amount,organic_units_quantity")


def _campanhas(periodo):
    adv = _advertiser_id()
    di, df = _datas(periodo)
    base = f"/advertising/advertisers/{adv}/product_ads/campaigns"
    params = {"limit": 50, "offset": 0, "date_from": di, "date_to": df,
              "metrics": METRICAS, "metrics_summary": "true"}
    r = _get(base, params, api_version="2")
    if r.get("_erro"):
        return None, None, di, df
    resultados = r.get("results", [])
    total = int((r.get("paging") or {}).get("total") or len(resultados))
    while len(resultados) < total:
        params["offset"] = len(resultados)
        pg = _get(base, params, api_version="2")
        novos = pg.get("results", [])
        if not novos:
            break
        resultados.extend(novos)
    return resultados, r.get("metrics_summary"), di, df


# ── 📊 GAVETA 1: ADS ────────────────────────────────────────────────────────

@mcp.tool()
def faturamento_consolidado(periodo: str = "semanal") -> str:
    """FATURAMENTO TOTAL consolidado: receita ADS + orgânico reais, gastos, ROAS global.
    periodo: hoje | ontem | semanal | quinzenal | mensal | mes_atual | AAAA-MM-DD:AAAA-MM-DD"""
    _, resumo, di, df = _campanhas(periodo)
    if resumo is None:
        return "Erro ao consultar a API do ML. Verifique a conexão."

    # Dados de ADS (100% oficiais)
    cost = float(resumo.get("cost") or 0)
    receita_ads = float(resumo.get("total_amount") or 0)
    units_ads = int(resumo.get("units_quantity") or 0)
    units_org_ads = int(resumo.get("organic_units_quantity") or 0)

    # Pedidos reais via /orders/search (faturamento total oficial)
    pedidos, erro_pedidos = _buscar_pedidos(di, df)

    if pedidos:
        receita_total = pedidos["receita"]
        unidades_total = pedidos["unidades"]
        receita_org = round(max(receita_total - receita_ads, 0), 2)
        units_org = max(unidades_total - units_ads, 0)
        fonte = "100% oficial ML — pedidos reais + ADS API"
        aviso = None
    else:
        # Fallback: estimativa se orders API falhar
        receita_org = 0
        if units_org_ads > 0 and receita_ads > 0 and units_ads > 0:
            preco_medio = receita_ads / units_ads
            receita_org = round(preco_medio * units_org_ads, 2)
        receita_total = receita_ads + receita_org
        unidades_total = units_ads + units_org_ads
        units_org = units_org_ads
        fonte = "ADS oficial + orgânico estimado (fallback)"
        aviso = f"⚠️ Pedidos reais indisponíveis ({erro_pedidos}). Orgânico é estimativa — pode ter diferença de 15-20% do painel ML."

    roas_global = round(receita_total / cost, 2) if cost else 0

    resultado = {
        "periodo": f"{di} a {df}",
        "faturamento_total": receita_total,
        "receita_ads": receita_ads,
        "receita_organica": receita_org,
        "gasto_ads": cost,
        "roas_global": roas_global,
        "unidades_ads": units_ads,
        "unidades_organicas": units_org,
        "unidades_total": unidades_total,
        "cliques": resumo.get("clicks"),
        "impressoes": resumo.get("prints"),
        "acos_pct": resumo.get("acos"),
        "cpc": resumo.get("cpc"),
        "fonte": fonte,
    }
    if aviso:
        resultado["aviso"] = aviso
    return json.dumps(resultado, ensure_ascii=False)


@mcp.tool()
def ads_resumo(periodo: str = "semanal") -> str:
    """Resumo geral do Product Ads da conta: receita, gasto, ROAS, ACOS, CPC, vendas.
    periodo: hoje | ontem | semanal | quinzenal | mensal | mes_atual | AAAA-MM-DD:AAAA-MM-DD"""
    _, resumo, di, df = _campanhas(periodo)
    if resumo is None:
        return "Erro ao consultar a API do ML. Verifique a conexão (conectar_ml.bat)."
    cost = float(resumo.get("cost") or 0)
    total = float(resumo.get("total_amount") or 0)
    roas = round(total / cost, 2) if cost else 0
    return json.dumps({
        "periodo": f"{di} a {df}",
        "receita_ads": total,
        "gasto": cost,
        "roas": roas,
        "acos_pct": resumo.get("acos"),
        "cpc": resumo.get("cpc"),
        "cliques": resumo.get("clicks"),
        "impressoes": resumo.get("prints"),
        "vendas_ads": resumo.get("units_quantity"),
        "vendas_organicas": resumo.get("organic_units_quantity"),
        "fonte": "API oficial ML, consultada agora",
    }, ensure_ascii=False)


@mcp.tool()
def ads_campanhas(periodo: str = "semanal") -> str:
    """Lista todas as campanhas Product Ads com métricas do período e sugestão de ação
    (ESCALAR/MANTER/REDUZIR/PAUSAR). periodo: hoje | ontem | semanal | quinzenal | mensal"""
    resultados, _, di, df = _campanhas(periodo)
    if resultados is None:
        return "Erro ao consultar a API do ML."
    out = []
    for c in resultados:
        m = c.get("metrics") or {}
        cost = float(m.get("cost") or 0)
        total = float(m.get("total_amount") or 0)
        roas = round(total / cost, 2) if cost else 0
        vendas = int(m.get("units_quantity") or 0)
        if c.get("status") != "active":
            acao = "—"
        elif cost >= 5 and vendas == 0:
            acao = "PAUSAR (gasto sem nenhuma venda)"
        elif roas >= 12:
            acao = "ESCALAR (confirme com 15 dias antes)"
        elif 0 < roas < 5 and cost > 0:
            acao = "REDUZIR (confirme com 15 dias antes)"
        else:
            acao = "MANTER"
        out.append({
            "nome": c.get("name"), "status": c.get("status"),
            "receita": total, "gasto": cost, "roas": roas,
            "acos_pct": m.get("acos"), "cliques": m.get("clicks"),
            "vendas": vendas, "orcamento_diario": c.get("budget"),
            "roas_alvo": c.get("roas_target"), "sugestao": acao,
        })
    out.sort(key=lambda x: -x["receita"])
    return json.dumps({"periodo": f"{di} a {df}", "campanhas": out,
                       "regra_de_ouro": "Nunca altere sem 3+ dias de dados e confirmação em janela maior."},
                      ensure_ascii=False)


@mcp.tool()
def ads_por_anuncio(periodo: str = "semanal") -> str:
    """Métricas de ADS por ANÚNCIO individual (qual produto puxa o resultado de cada
    campanha e qual só gasta). periodo: hoje | ontem | semanal | quinzenal | mensal"""
    adv = _advertiser_id()
    di, df = _datas(periodo)
    base = f"/advertising/advertisers/{adv}/product_ads/items"
    params = {"limit": 50, "offset": 0, "date_from": di, "date_to": df,
              "metrics": METRICAS}
    r = _get(base, params, api_version="2")
    if r.get("_erro"):
        params["metrics"] = "clicks,prints,cost,units_quantity,total_amount"
        r = _get(base, params, api_version="2")
        if r.get("_erro"):
            return "Erro ao consultar anúncios promovidos."
    resultados = r.get("results", [])
    total = int((r.get("paging") or {}).get("total") or len(resultados))
    while len(resultados) < total:
        params["offset"] = len(resultados)
        pg = _get(base, params, api_version="2")
        novos = pg.get("results", [])
        if not novos:
            break
        resultados.extend(novos)
    out = []
    for a in resultados:
        m = a.get("metrics") or {}
        cost = float(m.get("cost") or 0)
        rec = float(m.get("total_amount") or 0)
        if cost == 0 and rec == 0 and a.get("status") != "active":
            continue
        out.append({
            "item_id": a.get("item_id"), "titulo": (a.get("title") or "")[:60],
            "campanha_id": a.get("campaign_id"), "status": a.get("status"),
            "preco": a.get("price"), "ganhando_buybox": a.get("buy_box_winner"),
            "receita": rec, "gasto": cost,
            "roas": round(rec / cost, 2) if cost else 0,
            "cliques": m.get("clicks"), "impressoes": m.get("prints"),
            "vendas": m.get("units_quantity"),
        })
    out.sort(key=lambda x: -x["receita"])
    return json.dumps({"periodo": f"{di} a {df}", "total_anuncios_promovidos": total,
                       "anuncios": out}, ensure_ascii=False)


# ── 🏷️ GAVETA 2: ANÚNCIOS ───────────────────────────────────────────────────

@mcp.tool()
def meus_anuncios() -> str:
    """Lista os anúncios ativos da conta (id, título, preço, estoque)."""
    me = _get("/users/me")
    uid = me.get("id")
    if not uid:
        return "Erro: não foi possível identificar o usuário."
    ids, offset = [], 0
    while True:
        r = _get(f"/users/{uid}/items/search", {"status": "active", "limit": 50, "offset": offset})
        page = r.get("results", [])
        ids.extend(page)
        if len(ids) >= int((r.get("paging") or {}).get("total") or 0) or not page:
            break
        offset += 50
    out = []
    for i in range(0, len(ids), 20):
        lote = ",".join(ids[i:i+20])
        r = _get("/items", {"ids": lote, "attributes": "id,title,price,available_quantity,sold_quantity"})
        for item in (r if isinstance(r, list) else []):
            b = item.get("body", {})
            if b.get("id"):
                out.append({"id": b["id"], "titulo": b.get("title"), "preco": b.get("price"),
                            "estoque": b.get("available_quantity"), "vendidos": b.get("sold_quantity")})
    return json.dumps({"total": len(out), "anuncios": out}, ensure_ascii=False)


@mcp.tool()
def anuncio_completo(item_id: str) -> str:
    """Dados completos de um anúncio: título, preço, fotos, estoque, frete, catálogo, reviews.
    item_id: código MLB do anúncio (ex.: MLB1234567890)"""
    item = _get(f"/items/{item_id}")
    if item.get("_erro"):
        return f"Erro: anúncio {item_id} não encontrado."
    rev = _get(f"/reviews/item/{item_id}")
    return json.dumps({
        "id": item_id, "titulo": item.get("title"), "preco": item.get("price"),
        "status": item.get("status"), "estoque": item.get("available_quantity"),
        "vendidos": item.get("sold_quantity"), "fotos": len(item.get("pictures", [])),
        "tem_video": bool(item.get("video_id")),
        "frete_gratis": bool((item.get("shipping") or {}).get("free_shipping")),
        "catalogo": bool(item.get("catalog_listing")) or bool(item.get("catalog_product_id")),
        "catalog_product_id": item.get("catalog_product_id"),
        "tipo": item.get("listing_type_id"), "criado_em": item.get("date_created"),
        "reviews": {"total": (rev.get("paging") or {}).get("total", 0),
                    "nota": rev.get("rating_average", 0)},
    }, ensure_ascii=False)


@mcp.tool()
def anuncio_visitas(item_id: str, dias: int = 15) -> str:
    """Visitas diárias de um anúncio nos últimos N dias (mede tráfego e tendência)."""
    v = _get(f"/items/{item_id}/visits/time_window", {"last": dias, "unit": "day"})
    if v.get("_erro"):
        return f"Erro ao buscar visitas de {item_id}."
    return json.dumps({"item": item_id, "dias": dias, "total_visitas": v.get("total_visits"),
                       "por_dia": [{"data": x.get("date", "")[:10], "visitas": x.get("total")}
                                   for x in v.get("results", [])]}, ensure_ascii=False)


@mcp.tool()
def anuncio_buybox(item_id: str) -> str:
    """Situação do anúncio no catálogo/buybox: ganhando, dividindo ou perdendo,
    e qual preço seria necessário para ganhar."""
    p = _get(f"/items/{item_id}/price_to_win", {"siteId": "MLB"})
    if p.get("_erro"):
        return f"O anúncio {item_id} não participa de catálogo (sem buybox) ou houve erro."
    return json.dumps({
        "item": item_id, "situacao": p.get("status"),
        "preco_atual": p.get("current_price"), "preco_para_ganhar": p.get("price_to_win"),
        "melhorias_disponiveis": [b.get("id") for b in (p.get("boosts") or [])
                                  if isinstance(b, dict) and not b.get("status")],
    }, ensure_ascii=False)


@mcp.tool()
def anuncio_qualidade(item_id: str) -> str:
    """Raio-X de QUALIDADE de um anúncio para análise de bom anúncio: título (texto
    e tamanho), descrição (texto completo), ficha técnica (atributos preenchidos x
    vazios), nº de fotos e vídeo. Use junto com o método de análise de anúncio para
    avaliar título, descrição, keywords e completude. item_id: código MLB."""
    item = _get(f"/items/{item_id}")
    if item.get("_erro"):
        return f"Erro: anúncio {item_id} não encontrado."
    desc = _get(f"/items/{item_id}/description")
    texto_desc = "" if desc.get("_erro") else (desc.get("plain_text") or desc.get("text") or "")
    attrs = item.get("attributes") or []
    preenchidos = [a.get("name") for a in attrs if a.get("value_name")]
    vazios = [a.get("name") for a in attrs if not a.get("value_name")]
    titulo = item.get("title") or ""
    return json.dumps({
        "id": item_id,
        "titulo": {"texto": titulo, "tamanho": len(titulo),
                   "limite": 60, "usa_limite": f"{len(titulo)}/60 caracteres"},
        "descricao": {"texto": texto_desc, "tamanho": len(texto_desc),
                      "tem_descricao": bool(texto_desc.strip())},
        "ficha_tecnica": {"total_atributos": len(attrs),
                          "preenchidos": len(preenchidos), "vazios": len(vazios),
                          "atributos_vazios": vazios[:20]},
        "midia": {"fotos": len(item.get("pictures", [])),
                  "tem_video": bool(item.get("video_id"))},
        "frete_gratis": bool((item.get("shipping") or {}).get("free_shipping")),
        "catalogo": bool(item.get("catalog_listing")) or bool(item.get("catalog_product_id")),
    }, ensure_ascii=False)


PRECOS_HIST = SCRIPT_DIR / "precos_historico.json"


def registrar_precos(itens):
    """Acrescenta um snapshot {data: {item_id: preco}} ao histórico (1 por dia).
    Chamado pelo coletor matinal para alimentar a regra do menor preço D-30."""
    try:
        hist = json.load(open(PRECOS_HIST, encoding="utf-8")) if PRECOS_HIST.exists() else {}
    except Exception:
        hist = {}
    hoje = datetime.now().strftime("%Y-%m-%d")
    hist[hoje] = {str(i["id"]): float(i.get("preco") or 0) for i in itens if i.get("id")}
    # mantém ~40 dias
    for d in sorted(hist)[:-40]:
        hist.pop(d, None)
    json.dump(hist, open(PRECOS_HIST, "w", encoding="utf-8"), ensure_ascii=False)


@mcp.tool()
def preco_d30(item_id: str) -> str:
    """Regra do MENOR PREÇO DOS ÚLTIMOS 30 DIAS (D-30) do ML: para anunciar um
    desconto, o preço 'de' não pode ser maior que o menor preço praticado em 30 dias.
    Mostra o preço atual, o de referência da API e o menor preço do histórico local
    (construído pelo coletor matinal). item_id: MLB."""
    p = _get(f"/items/{item_id}/prices")
    atual = ref = None
    for pr in (p.get("prices") or []):
        if pr.get("type") == "standard":
            atual = pr.get("amount"); ref = pr.get("regular_amount")
    hist_min = None
    dias = 0
    if PRECOS_HIST.exists():
        try:
            hist = json.load(open(PRECOS_HIST, encoding="utf-8"))
            corte = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            vals = [d[str(item_id)] for dia, d in hist.items()
                    if dia >= corte and str(item_id) in d and d[str(item_id)] > 0]
            dias = len(vals)
            if vals:
                hist_min = min(vals)
        except Exception:
            pass
    alerta = None
    if hist_min and atual and ref and ref > hist_min:
        alerta = (f"⚠️ O preço 'de' (R${ref}) é maior que o menor preço dos últimos "
                  f"30 dias (R${hist_min}). Pela regra do ML o desconto pode ser invalidado.")
    return json.dumps({
        "item": item_id, "preco_atual": atual, "preco_de_referencia_api": ref,
        "menor_preco_30d_local": hist_min,
        "dias_de_historico": dias,
        "status_historico": ("completo" if dias >= 30 else
                             f"construindo ({dias}/30 dias) — rode o coletor matinal diariamente"),
        "alerta": alerta or "Sem inconsistência detectada com os dados disponíveis.",
    }, ensure_ascii=False)


@mcp.tool()
def curva_abc() -> str:
    """Classifica seus anúncios ativos na CURVA ABC por faturamento estimado
    (preço × vendidos): A = top 80% do faturamento (campeões), B = próximos 15%,
    C = últimos 5% (incluindo zumbis com 0 venda). É o método da planilha-mãe para
    separar o que sustenta a loja do que só ocupa espaço."""
    dados = json.loads(meus_anuncios())
    itens = []
    for a in dados.get("anuncios", []):
        fat = float(a.get("preco") or 0) * int(a.get("vendidos") or 0)
        itens.append({**a, "faturamento_est": round(fat, 2)})
    itens.sort(key=lambda x: -x["faturamento_est"])
    total = sum(i["faturamento_est"] for i in itens) or 1
    acum = 0.0
    a_list, b_list, c_list = [], [], []
    for i in itens:
        acum += i["faturamento_est"]
        pct = acum / total
        reg = {"id": i["id"], "titulo": (i.get("titulo") or "")[:50],
               "vendidos": i.get("vendidos"), "estoque": i.get("estoque"),
               "faturamento_est": i["faturamento_est"]}
        if pct <= 0.80:
            reg["curva"] = "A"; a_list.append(reg)
        elif pct <= 0.95:
            reg["curva"] = "B"; b_list.append(reg)
        else:
            reg["curva"] = "C"; c_list.append(reg)
    return json.dumps({
        "faturamento_total_estimado": round(total, 2),
        "resumo": {"A": len(a_list), "B": len(b_list), "C": len(c_list)},
        "A_campeoes": a_list, "B_intermediarios": b_list, "C_cauda_e_zumbis": c_list,
        "dica": "Proteja os A (estoque/preço), teste os B, decida pausar/diferenciar os C com 0 venda.",
    }, ensure_ascii=False)


def _cond_item(item_id):
    it = _get(f"/items/{item_id}")
    if it.get("_erro"):
        return None
    ship = it.get("shipping") or {}
    return {
        "id": item_id, "titulo": (it.get("title") or "")[:55],
        "preco": it.get("price"),
        "frete_gratis": bool(ship.get("free_shipping")),
        "tipo_logistica": ship.get("logistic_type"),  # fulfillment = Full
        "full": ship.get("logistic_type") == "fulfillment",
        "vendidos": it.get("sold_quantity"),
        "catalogo": bool(it.get("catalog_listing")) or bool(it.get("catalog_product_id")),
    }


@mcp.tool()
def comparar_condicoes(meu_item_id: str, concorrente_item_id: str) -> str:
    """Compara as CONDIÇÕES DE VENDA do seu anúncio vs um concorrente (método Max
    Cazonato): frete grátis, Full (fulfillment), preço e catálogo. É onde se descobre
    por que o concorrente vende mais mesmo você convertendo bem. Passe os dois MLB."""
    meu = _cond_item(meu_item_id)
    conc = _cond_item(concorrente_item_id)
    if not meu or not conc:
        return "Erro: um dos anúncios não foi encontrado."
    gaps = []
    if conc["frete_gratis"] and not meu["frete_gratis"]:
        gaps.append("Concorrente tem FRETE GRÁTIS e você não.")
    if conc["full"] and not meu["full"]:
        gaps.append("Concorrente usa FULL (entrega ML) e você não — Full ranqueia mais.")
    if meu["preco"] and conc["preco"] and conc["preco"] < meu["preco"]:
        gaps.append(f"Concorrente está mais barato (R${conc['preco']} vs R${meu['preco']}).")
    return json.dumps({"voce": meu, "concorrente": conc,
                       "gaps": gaps or ["Nenhuma desvantagem óbvia nas condições — foque em título/ADS/reviews."],
                       "obs": "Parcelamento sem juros não vem na API; confira manualmente no anúncio do concorrente."},
                      ensure_ascii=False)


_REASON_FAM = {
    "PDD": "Produto com defeito / avariado (danificado)",
    "PNR": "Produto não recebido / problema no envio",
    "PMD": "Produto diferente do anunciado",
    "PDM": "Produto com peças faltando",
}


@mcp.tool()
def reclamacoes_anuncio(item_id: str, dias: int = 90) -> str:
    """Diagnóstico INVISÍVEL: cruza as reclamações pós-venda (claims/mediações) com os
    pedidos para descobrir quantas e por que motivo um ANÚNCIO recebeu reclamações.
    É a causa nº1 de um anúncio saudável parar de vender: reclamações derrubam a
    'experiência de compra', o ML reduz a exposição e pode bloquear o Product Ads.
    As avaliações públicas (estrelas) NÃO mostram isso. item_id: MLB."""
    achados = []
    for status in ("opened", "closed"):
        offset = 0
        while True:
            r = _get("/post-purchase/v1/claims/search",
                     {"status": status, "limit": 50, "offset": offset})
            if r.get("_erro"):
                break
            data = r.get("data", [])
            if not data:
                break
            for c in data:
                if c.get("resource") != "order":
                    continue
                time.sleep(0.3)  # evita rate limit em loop de reclamações
                o = _get(f"/orders/{c.get('resource_id')}")
                itens = [(it.get("item") or {}).get("id") for it in (o.get("order_items") or [])]
                if str(item_id) in itens:
                    rid = c.get("reason_id") or ""
                    achados.append({
                        "claim_id": c.get("id"), "status": status,
                        "tipo": c.get("type"), "stage": c.get("stage"),
                        "reason_id": rid,
                        "motivo": _REASON_FAM.get(rid[:3], "Outro motivo"),
                        "data": (c.get("date_created") or "")[:10],
                        "eh_mediacao": c.get("type") == "mediations",
                    })
            offset += 50
            if offset >= (r.get("paging") or {}).get("total", 0):
                break
    mediacoes = sum(1 for a in achados if a["eh_mediacao"])
    por_motivo = {}
    for a in achados:
        por_motivo[a["motivo"]] = por_motivo.get(a["motivo"], 0) + 1
    diag = ("Nenhuma reclamação encontrada — não é causa de queda." if not achados else
            f"{len(achados)} reclamações ({mediacoes} mediações). Reclamações derrubam a "
            "experiência de compra → menos exposição → ADS pode ser bloqueado. "
            "Corrija a causa (ex.: embalagem) e resolva as mediações; a recuperação é "
            "janela móvel (~60 dias).")
    return json.dumps({
        "item": item_id, "total_reclamacoes": len(achados), "mediacoes": mediacoes,
        "por_motivo": por_motivo, "reclamacoes": achados, "diagnostico": diag,
    }, ensure_ascii=False)


@mcp.tool()
def vendedor_do_anuncio(item_id: str) -> str:
    """Descobre QUAL VENDEDOR (seller_id + nickname) está por trás de um anúncio
    concorrente. Use o seller_id retornado em monitorar_concorrente. item_id: MLB do
    anúncio do concorrente."""
    item = _get(f"/items/{item_id}")
    if item.get("_erro"):
        return f"Erro: anúncio {item_id} não encontrado."
    sid = item.get("seller_id")
    nick = None
    if sid:
        u = _get(f"/users/{sid}")
        nick = (u or {}).get("nickname")
    return json.dumps({"item": item_id, "seller_id": sid, "nickname": nick,
                       "titulo": (item.get("title") or "")[:60]}, ensure_ascii=False)


@mcp.tool()
def monitorar_concorrente(seller_id: str) -> str:
    """Espiona um VENDEDOR concorrente via API pública: reputação, nível, status de
    power seller, total de transações (histórico) e % de avaliações. É o método
    'monitorar vendedor a vendedor'. Use vendedor_do_anuncio para achar o seller_id.
    Observação: o ML não expõe o faturamento (TGMV) do concorrente — só transações; o
    faturamento é estimável multiplicando por um ticket médio que você informar."""
    u = _get(f"/users/{seller_id}")
    if u.get("_erro"):
        return f"Erro: vendedor {seller_id} não encontrado."
    rep = u.get("seller_reputation") or {}
    tx = rep.get("transactions") or {}
    ratings = tx.get("ratings") or {}
    return json.dumps({
        "seller_id": seller_id,
        "nickname": u.get("nickname"),
        "cadastro_desde": (u.get("registration_date") or "")[:10],
        "nivel": rep.get("level_id"),
        "power_seller": rep.get("power_seller_status"),
        "transacoes": {"total": tx.get("total"), "completadas": tx.get("completed"),
                       "canceladas": tx.get("canceled"), "periodo": tx.get("period")},
        "avaliacoes": {"positivas": ratings.get("positive"),
                       "neutras": ratings.get("neutral"),
                       "negativas": ratings.get("negative")},
        "dica": ("Acompanhe o 'total' de transações ao longo do tempo para ver quem "
                 "cresce. Faturamento estimado = transações no período × ticket médio."),
    }, ensure_ascii=False)


@mcp.tool()
def analisar_concorrentes(termo: str, ticket_medio: float = 0, limite: int = 6) -> str:
    """Análise COMPLETA de concorrentes: busca os top anúncios do ML para o termo,
    descobre o vendedor de cada um e traz reputação, nível, total de transações e
    estimativa de faturamento. Tem fallback automático para catálogo se a busca principal
    estiver indisponível. Exemplo: analisar_concorrentes('lixeira inox pedal 12l',
    ticket_medio=97.90)"""

    # ── TENTATIVA 1: busca de listings (dados de vendedor) ──────────────────
    busca = _get("/sites/MLB/search", {"q": termo, "limit": limite})
    if not busca.get("_erro"):
        resultados = busca.get("results") or []
        concorrentes = []
        sellers_vistos = set()

        for item in resultados:
            seller_info = item.get("seller") or {}
            sid = seller_info.get("id")
            if not sid or sid in sellers_vistos:
                continue
            sellers_vistos.add(sid)

            time.sleep(0.4)  # respeita rate limit: ~2 req/s por seller
            u = _get(f"/users/{sid}")
            rep = (u.get("seller_reputation") or {}) if not u.get("_erro") else {}
            tx = rep.get("transactions") or {}
            ratings = tx.get("ratings") or {}
            total_tx = tx.get("total") or 0
            fat_estimado = round(total_tx * ticket_medio, 2) if ticket_medio else None

            concorrentes.append({
                "item_id": item.get("id"),
                "titulo": (item.get("title") or "")[:70],
                "preco": item.get("price"),
                "vendedor": seller_info.get("nickname") or u.get("nickname"),
                "seller_id": sid,
                "nivel": rep.get("level_id"),
                "power_seller": rep.get("power_seller_status"),
                "cadastro_desde": (u.get("registration_date") or "")[:10],
                "transacoes_total": total_tx,
                "transacoes_completas": tx.get("completed"),
                "avaliacao_positiva_pct": ratings.get("positive"),
                "faturamento_estimado": fat_estimado,
            })

        return json.dumps({
            "termo": termo,
            "fonte": "listings ML (dados completos de vendedor)",
            "ticket_medio_usado": ticket_medio or "não informado",
            "total_encontrados": len(concorrentes),
            "concorrentes": concorrentes,
            "nota": ("faturamento_estimado = transações históricas × ticket_medio. "
                     "Transações históricas acumulam desde a criação da conta — "
                     "use como indicador de força, não faturamento mensal."),
        }, ensure_ascii=False)

    # ── FALLBACK: API de busca bloqueada → usa catálogo (pesquisar_concorrentes) ──
    # Ocorre quando o ML aplica rate-limit temporário na busca de listings.
    # O catálogo é mais estável e retorna os produtos líderes de cada categoria.
    erro_original = busca.get("_msg", "")
    catalogo = _get("/products/search", {"site_id": "MLB", "q": termo, "limit": limite})

    if catalogo.get("_erro"):
        # Ambas as fontes falharam — devolve mensagem clara para o aluno
        return json.dumps({
            "aviso": (
                f"A API do Mercado Livre está temporariamente limitando buscas para '{termo}'. "
                "Isso é normal e passa em alguns minutos. "
                "O que você pode fazer agora: "
                "(1) Aguarde 2-3 minutos e tente de novo. "
                "(2) Use pesquisar_concorrentes('" + termo + "') — usa outra rota da API. "
                "(3) Pesquise manualmente em mercadolivre.com.br enquanto aguarda."
            ),
            "alternativa_sugerida": f"pesquisar_concorrentes('{termo}')",
        }, ensure_ascii=False)

    # Catálogo disponível — retorna o que tem com nota explicativa
    titulos = [
        {
            "catalog_id": p.get("id"),
            "nome": p.get("name"),
            "link": f"https://www.mercadolivre.com.br/p/{p.get('id')}",
        }
        for p in (catalogo.get("results") or [])
        if isinstance(p, dict) and p.get("name")
    ]

    return json.dumps({
        "termo": termo,
        "fonte": "FALLBACK — catálogo ML (busca de listings temporariamente indisponível)",
        "aviso": (
            "A rota de listings do ML está com rate-limit agora. "
            "Os dados abaixo vêm do catálogo (sem dados de vendedor). "
            "Tente analisar_concorrentes novamente em 2-3 minutos para dados completos."
        ),
        "produtos_líderes": titulos,
        "total_encontrados": len(titulos),
        "nota": "Para dados de vendedor (reputação, transações), repita o comando em alguns minutos.",
    }, ensure_ascii=False)


@mcp.tool()
def pesquisar_concorrentes(termo: str, limite: int = 8) -> str:
    """Inteligência de concorrência (dados públicos): busca os produtos de CATÁLOGO
    concorrentes para um termo (ex.: 'kit chave catraca soquete') e devolve os títulos
    campeões + a categoria prevista + os atributos que os líderes preenchem. Use para
    extrair keywords REAIS dos títulos que vendem e descobrir que ficha técnica completar.
    termo: o que o cliente digitaria na busca do ML."""
    busca = _get("/products/search", {"site_id": "MLB", "q": termo, "limit": limite})
    if busca.get("_erro"):
        return json.dumps({"erro": f"Busca de catálogo indisponível ({busca['_erro']}). "
                           "Use ferramentas externas de pesquisa (Metrify/Avantpro) para keywords."},
                          ensure_ascii=False)
    titulos = [{"nome": p.get("name"), "catalog_id": p.get("id")}
               for p in (busca.get("results") or []) if isinstance(p, dict) and p.get("name")]
    # categoria prevista para o termo
    dom = _get("/sites/MLB/domain_discovery/search", {"q": termo})
    categoria = None
    if isinstance(dom, list) and dom:
        categoria = {"categoria": dom[0].get("category_name"),
                     "category_id": dom[0].get("category_id")}
    # atributos que o líder preenche (amostra do 1º produto)
    atributos_lider = []
    if titulos:
        d = _get(f"/products/{titulos[0]['catalog_id']}")
        if not d.get("_erro"):
            for a in (d.get("attributes") or [])[:12]:
                if isinstance(a, dict) and a.get("value_name"):
                    atributos_lider.append(f"{a.get('name')}: {a.get('value_name')}")
    return json.dumps({
        "termo_buscado": termo,
        "categoria_prevista": categoria,
        "titulos_concorrentes": titulos,
        "atributos_do_lider": atributos_lider,
        "dica": ("Extraia das palavras que mais se repetem nos títulos as keywords reais; "
                 "compare a ficha do líder com a sua para ver o que falta preencher."),
    }, ensure_ascii=False)


@mcp.tool()
def estoque_alertas() -> str:
    """Anúncios ativos com estoque crítico (≤5 un.) ou baixo (≤15 un.)."""
    dados = json.loads(meus_anuncios())
    alertas = []
    for a in dados.get("anuncios", []):
        est = a.get("estoque") or 0
        if est <= 5:
            alertas.append({**a, "nivel": "CRÍTICO"})
        elif est <= 15:
            alertas.append({**a, "nivel": "baixo"})
    return json.dumps({"alertas": alertas or "Nenhum alerta de estoque ✅"}, ensure_ascii=False)


# ── ⚙️ GAVETA 3: OPERAÇÃO (Modo Operação — chave MLO) ──────────────────────

REGRA_OURO = ("Regra de Ouro: nunca altere sem 3+ dias de dados após a última "
              "mudança + confirmação em janela maior + comparativo anterior.")


def _campanha_atual(campaign_id):
    # o endpoint de campanha individual não existe na API oficial (404);
    # busca na listagem, que cobre todas as campanhas da conta
    adv = _advertiser_id()
    resultados, _, _, _ = _campanhas("ontem")
    for c in resultados or []:
        if str(c.get("id")) == str(campaign_id):
            return adv, c
    raise RuntimeError(f"Campanha {campaign_id} não encontrada na conta.")


PASSO_A_PASSO_PAINEL = (
    "Mercado Livre → Vendas → Publicidade → Product Ads → Campanhas → "
    "encontre a campanha pelo nome → use o botão de status (pausar/ativar) "
    "ou clique no orçamento para editar.")


@mcp.tool()
def pausar_campanha(campaign_id: str) -> str:
    """[MODO OPERAÇÃO] Prepara a pausa de uma campanha: mostra o estado atual e o
    passo a passo para o usuário pausar MANUALMENTE no painel do ML. Por segurança,
    campanhas nunca são alteradas via API — a execução é sempre do usuário."""
    _checar_modo_operacao()
    _, c = _campanha_atual(campaign_id)
    return json.dumps({
        "acao_recomendada": f"PAUSAR campanha {c.get('name')} (id {campaign_id})",
        "campanha": {"id": c.get("id"), "nome": c.get("name"),
                     "status": c.get("status"), "orcamento_diario": c.get("budget")},
        "como_fazer": PASSO_A_PASSO_PAINEL,
        "importante": ("Esta ferramenta NÃO altera nada — a pausa é manual, "
                       "feita pelo usuário no painel. Confirme depois com ads_campanhas."),
        "regra_de_ouro": REGRA_OURO}, ensure_ascii=False)


@mcp.tool()
def retomar_campanha(campaign_id: str) -> str:
    """[MODO OPERAÇÃO] Prepara a reativação de uma campanha pausada: estado atual +
    passo a passo para o usuário ativar MANUALMENTE no painel do ML."""
    _checar_modo_operacao()
    _, c = _campanha_atual(campaign_id)
    return json.dumps({
        "acao_recomendada": f"RETOMAR campanha {c.get('name')} (id {campaign_id})",
        "campanha": {"id": c.get("id"), "nome": c.get("name"),
                     "status": c.get("status"), "orcamento_diario": c.get("budget")},
        "como_fazer": PASSO_A_PASSO_PAINEL,
        "importante": ("Esta ferramenta NÃO altera nada — a reativação é manual, "
                       "feita pelo usuário no painel. Confirme depois com ads_campanhas."),
        "regra_de_ouro": REGRA_OURO}, ensure_ascii=False)


@mcp.tool()
def alterar_orcamento_campanha(campaign_id: str, novo_orcamento: float) -> str:
    """[MODO OPERAÇÃO] Prepara a mudança de orçamento diário: valida o limite de
    segurança (±30% por mudança) e dá o passo a passo para o usuário alterar
    MANUALMENTE no painel do ML."""
    _checar_modo_operacao()
    _, atual = _campanha_atual(campaign_id)
    orc = float(atual.get("budget") or 0)
    if orc > 0:
        var = abs(novo_orcamento - orc) / orc
        if var > 0.30:
            return json.dumps({"recusado": (
                f"Variação de {var*100:.0f}% excede o limite de segurança de ±30% "
                f"por alteração (atual R${orc:.2f} → pedido R${novo_orcamento:.2f}). "
                "Faça mudanças graduais."), "regra_de_ouro": REGRA_OURO},
                ensure_ascii=False)
    return json.dumps({
        "acao_recomendada": (f"ORÇAMENTO da campanha {atual.get('name')} "
                             f"(id {campaign_id}): R${orc:.2f} → R${novo_orcamento:.2f}/dia"),
        "como_fazer": PASSO_A_PASSO_PAINEL,
        "importante": ("Esta ferramenta NÃO altera nada — a mudança é manual, "
                       "feita pelo usuário no painel. Confirme depois com ads_campanhas."),
        "regra_de_ouro": REGRA_OURO}, ensure_ascii=False)


@mcp.tool()
def alterar_preco_anuncio(item_id: str, novo_preco: float, confirmo: bool = False) -> str:
    """[MODO OPERAÇÃO] Altera o preço de um anúncio. Limite de segurança: ±20%
    por chamada. Confirmação dupla obrigatória."""
    _checar_modo_operacao()
    item = _get(f"/items/{item_id}")
    if item.get("_erro"):
        return f"Erro: anúncio {item_id} não encontrado."
    preco = float(item.get("price") or 0)
    estado = {"id": item_id, "titulo": (item.get("title") or "")[:60], "preco_atual": preco}
    if preco > 0 and abs(novo_preco - preco) / preco > 0.20:
        return json.dumps({"recusado": (
            f"Variação excede o limite de segurança de ±20% por alteração "
            f"(atual R${preco:.2f} → pedido R${novo_preco:.2f})."),
            "regra_de_ouro": REGRA_OURO}, ensure_ascii=False)
    if not confirmo:
        return json.dumps({
            "acao_pendente": f"ALTERAR PREÇO de {item_id} para R${novo_preco:.2f}",
            "anuncio": estado,
            "instrucao": ("NÃO execute ainda. Mostre ao usuário, peça confirmação "
                          "explícita e chame de novo com confirmo=true."),
            "regra_de_ouro": REGRA_OURO}, ensure_ascii=False)
    r = _put(f"/items/{item_id}", {"price": novo_preco})
    if r.get("_erro"):
        return json.dumps({"erro": f"ML recusou ({r['_erro']}): {r.get('_msg')}",
                           "anuncio": estado}, ensure_ascii=False)
    return json.dumps({"executado": f"Preço de {item_id}: R${preco:.2f} → R${novo_preco:.2f}",
                       "regra_de_ouro": REGRA_OURO}, ensure_ascii=False)


@mcp.tool()
def relatorio_matinal() -> str:
    """[MODO OPERAÇÃO] Relatório matinal pronto (gerado automaticamente às 07:50):
    resumo de ontem/7d/15d/30d, sugestões por campanha e alertas de estoque.
    Use quando o usuário disser 'bom dia' ou pedir o relatório do dia."""
    _checar_modo_operacao()
    arq = SCRIPT_DIR / "relatorio_matinal_aluno.json"
    if arq.exists():
        rel = json.load(open(arq, encoding="utf-8"))
        try:
            idade_h = (datetime.now() - datetime.fromisoformat(rel.get("gerado_em", ""))).total_seconds() / 3600
        except Exception:
            idade_h = 999
        if idade_h > 26:
            rel["aviso"] = (f"⚠️ Relatório tem {idade_h:.0f}h — a tarefa agendada pode ter "
                            "falhado. Considere usar ads_resumo para dados ao vivo.")
        return json.dumps(rel, ensure_ascii=False)
    fat_ontem = json.loads(faturamento_consolidado("ontem"))
    ads_ontem = json.loads(ads_resumo("ontem"))
    camp_ontem = json.loads(ads_campanhas("ontem"))
    fat_7d = json.loads(faturamento_consolidado("semanal"))
    est = json.loads(estoque_alertas())
    return json.dumps({
        "gerado_em": datetime.now().isoformat(),
        "modo": "ao_vivo",
        "nota": "Gerado ao vivo via API (sem coletor local)",
        "faturamento_ontem": fat_ontem,
        "ads_ontem": ads_ontem,
        "campanhas_ontem": camp_ontem,
        "faturamento_7d": fat_7d,
        "estoque_alertas": est,
    }, ensure_ascii=False)


@mcp.tool()
def ads_historico(semanas: int = 4) -> str:
    """[MODO OPERAÇÃO] Comparativo histórico semana a semana (acumulado pelo
    coletor matinal): receita, gasto, ROAS de cada coleta de 7 dias."""
    _checar_modo_operacao()
    arq = SCRIPT_DIR / "historico_aluno.json"
    if arq.exists():
        hist = json.load(open(arq, encoding="utf-8"))
        entradas = [h for h in hist if h.get("janela") == "semanal"][-semanas:]
        return json.dumps({"semanas": entradas,
                           "nota": "Cada entrada = janela móvel de 7 dias na data da coleta."},
                          ensure_ascii=False)
    semanal = json.loads(ads_resumo("semanal"))
    quinzenal = json.loads(ads_resumo("quinzenal"))
    mensal = json.loads(ads_resumo("mensal"))
    return json.dumps({
        "nota": "Histórico acumulado indisponível (sem coletor local). Dados ao vivo via API.",
        "semana_atual": semanal,
        "quinzena_atual": quinzenal,
        "mes_atual": mensal,
    }, ensure_ascii=False)


@mcp.tool()
def historico_campanhas(dias: int = 7) -> str:
    """Histórico diário de TODAS as campanhas ativas: impressões, cliques, ROAS,
    receita, investimento, ACOS por dia. Ideal para gráficos de evolução de desempenho.
    dias: quantos dias atrás consultar (padrão 7, máximo 14)."""
    dias = min(max(int(dias), 1), 14)
    hoje = datetime.now().date()
    serie = []

    for i in range(dias - 1, -1, -1):
        data = str(hoje - timedelta(days=i))
        time.sleep(0.5)  # evita rate limit ao consultar dia a dia
        campanhas, _, _, _ = _campanhas(f"{data}:{data}")
        if campanhas is None:
            continue

        dia_entry = {"data": data, "campanhas": []}
        for c in campanhas:
            metricas = c.get("metrics_summary") or c
            nome = c.get("name", "—")
            status = c.get("status", "")
            receita = float((c.get("metrics") or {}).get("total_amount") or
                            metricas.get("total_amount") or 0)
            gasto = float((c.get("metrics") or {}).get("cost") or
                          metricas.get("cost") or 0)
            cliques = int((c.get("metrics") or {}).get("clicks") or
                          metricas.get("clicks") or 0)
            impressoes = int((c.get("metrics") or {}).get("prints") or
                             metricas.get("prints") or 0)
            vendas = int((c.get("metrics") or {}).get("units_quantity") or
                         metricas.get("units_quantity") or 0)
            roas = round(receita / gasto, 2) if gasto else 0
            acos = round(gasto / receita * 100, 1) if receita else 0
            cpc = round(gasto / cliques, 2) if cliques else 0

            dia_entry["campanhas"].append({
                "nome": nome,
                "status": status,
                "impressoes": impressoes,
                "cliques": cliques,
                "cpc": cpc,
                "receita": round(receita, 2),
                "gasto": round(gasto, 2),
                "roas": roas,
                "acos_pct": acos,
                "vendas": vendas,
            })
        serie.append(dia_entry)

    nomes_ativos = sorted({
        c["nome"]
        for d in serie
        for c in d["campanhas"]
        if c["gasto"] > 0 or c["impressoes"] > 0
    })

    return json.dumps({
        "periodo": f"{hoje - timedelta(days=dias-1)} a {hoje}",
        "campanhas_com_atividade": nomes_ativos,
        "serie_diaria": serie,
    }, ensure_ascii=False)


@mcp.tool()
def pedidos_por_estado(dias: int = 30) -> str:
    """Agrupa pedidos pagos por estado (UF) com frete médio pago pelo vendedor.
    Mostra quais regiões custam mais frete e quanto representam das vendas.
    dias: quantos dias analisar (padrão 30, máximo 90). Analisa até 60 pedidos como amostra."""
    from collections import defaultdict

    dias = min(max(int(dias), 1), 90)
    df = datetime.now().strftime("%Y-%m-%d")
    di = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")

    try:
        seller = _seller_id()
    except Exception as e:
        return json.dumps({"erro": str(e)})

    # 1. Buscar pedidos e coletar shipping_ids (limite 60 para não sobrecarregar)
    shipping_items = []
    offset = 0
    total_periodo = 0
    while len(shipping_items) < 60:
        params = {
            "seller": seller,
            "order.date_created.from": f"{di}T00:00:00.000-0300",
            "order.date_created.to": f"{df}T23:59:59.000-0300",
            "order.status": "paid",
            "limit": 50,
            "offset": offset,
        }
        r = _get("/orders/search", params)
        if r.get("_erro"):
            return json.dumps({"erro": f"Erro ao buscar pedidos: {r.get('_msg', '')}"})
        resultados = r.get("results", [])
        total_periodo = int((r.get("paging") or {}).get("total") or 0)
        for order in resultados:
            ship_id = ((order.get("shipping") or {}).get("id"))
            if ship_id:
                shipping_items.append({
                    "ship_id": ship_id,
                    "total": float(order.get("total_amount") or 0),
                })
            if len(shipping_items) >= 60:
                break
        offset += len(resultados)
        if not resultados or offset >= total_periodo:
            break

    if not shipping_items:
        return json.dumps({"aviso": "Nenhum pedido encontrado no período.", "periodo": f"{di} a {df}"})

    # 2. Para cada envio buscar estado + custo frete via /shipments/{id}
    estados = defaultdict(lambda: {"pedidos": 0, "receita": 0.0, "frete_total": 0.0})
    sem_dados = 0
    for item in shipping_items:
        time.sleep(0.3)  # evita rate limit em loop de envios
        r = _get(f"/shipments/{item['ship_id']}")
        if r.get("_erro"):
            sem_dados += 1
            continue
        recv = r.get("receiver_address") or {}
        st = recv.get("state") or {}
        uf = st.get("name") or st.get("id") or "Desconhecido"
        frete = float(r.get("base_cost") or r.get("cost") or 0)
        estados[uf]["pedidos"] += 1
        estados[uf]["receita"] += item["total"]
        estados[uf]["frete_total"] += frete

    analisados = len(shipping_items) - sem_dados
    por_estado = []
    for uf, d in sorted(estados.items(), key=lambda x: -x[1]["pedidos"]):
        n = d["pedidos"]
        por_estado.append({
            "estado": uf,
            "pedidos": n,
            "pct_pedidos": round(n / analisados * 100, 1) if analisados else 0,
            "receita_total": round(d["receita"], 2),
            "frete_medio": round(d["frete_total"] / n, 2) if n else 0,
            "frete_total": round(d["frete_total"], 2),
        })

    frete_medio_geral = round(
        sum(d["frete_total"] for d in estados.values()) / analisados, 2
    ) if analisados else 0

    return json.dumps({
        "periodo": f"{di} a {df}",
        "total_pedidos_periodo": total_periodo,
        "pedidos_analisados": analisados,
        "aviso": f"Amostra de {analisados} dos {total_periodo} pedidos do período." if total_periodo > analisados else None,
        "frete_medio_geral": frete_medio_geral,
        "por_estado": por_estado,
    }, ensure_ascii=False)


if __name__ == "__main__":
    # Tentar rodar como MCP server se FastMCP estiver disponível
    try:
        mcp.run()
    except AttributeError:
        # MockMCP não tem run(), apenas print que está pronto
        print("[OK] Servidor pronto para usar no Claude Desktop!")

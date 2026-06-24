"""
TENANT STORE — resolve a chave do aluno (mc_live_...) para o config dele.

Dois modos (escolhido por variavel de ambiente):
  1. LOVABLE API — se EXTERNAL_API_KEY estiver definido:
        chama o endpoint do app Lovable que consulta ml_conexoes server-side
        (com a service role selada no Lovable Cloud) e ja refresca o token ML.
        GET  {LOVABLE_API_URL}?mcp_key=...   header x-api-key: EXTERNAL_API_KEY
        PATCH {LOVABLE_API_URL}              header x-api-key (write-back opcional)
  2. ARQUIVO — fallback: usa tenants.json (key -> config.json local) p/ testes.

Interface:
  resolve(key) -> config (dict OU Path) pronto pro servidor.py, ou None se invalida.
"""

import os
import json
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
TENANTS_FILE = SCRIPT_DIR / "tenants.json"

LOVABLE_API_URL = os.environ.get(
    "LOVABLE_API_URL", "https://analistaia.lovable.app/api/public/ml-conexoes"
)
EXTERNAL_API_KEY = os.environ.get("EXTERNAL_API_KEY", "")


def _modo_lovable() -> bool:
    return bool(EXTERNAL_API_KEY)


# ── Modo Lovable API ─────────────────────────────────────────────────────────

def _extrair_linha(payload):
    """Normaliza a resposta: aceita dict, lista [dict] ou {data: dict}."""
    if isinstance(payload, list):
        return payload[0] if payload else None
    if isinstance(payload, dict):
        if "access_token" in payload:
            return payload
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        if isinstance(payload.get("data"), list):
            return payload["data"][0] if payload["data"] else None
    return None


def _resolver_lovable(key: str):
    """Chama o endpoint do Lovable e devolve um config-dict (com _save via PATCH)."""
    try:
        r = requests.get(
            LOVABLE_API_URL,
            headers={"x-api-key": EXTERNAL_API_KEY},
            params={"mcp_key": key},
            timeout=15,
        )
    except Exception:
        return None

    if r.status_code != 200:
        return None  # 401 (api key) / 404 (inativo) / etc -> chave invalida

    row = _extrair_linha(r.json())
    if not row or not row.get("access_token"):
        return None

    config = {
        "access_token": row.get("access_token"),
        "refresh_token": row.get("refresh_token"),
        "expires_at": row.get("expires_at"),
        "seller_id": str(row.get("seller_id") or ""),
        "advertiser_id": str(row.get("advertiser_id") or row.get("seller_id") or ""),
    }

    # Write-back opcional: o endpoint do Lovable ja refresca o token sozinho,
    # entao normalmente isto nao sera chamado. Fica como rede de seguranca.
    def _save(novo):
        try:
            requests.patch(
                LOVABLE_API_URL,
                headers={"x-api-key": EXTERNAL_API_KEY},
                json={
                    "mcp_key": key,
                    "access_token": novo.get("access_token"),
                    "refresh_token": novo.get("refresh_token"),
                    "expires_at": novo.get("expires_at"),
                },
                timeout=10,
            )
        except Exception:
            pass

    config["_save"] = _save
    return config


# ── Modo arquivo (fallback) ──────────────────────────────────────────────────

def _resolver_arquivo(key: str):
    if not TENANTS_FILE.exists():
        return None
    tenants = json.load(open(TENANTS_FILE, encoding="utf-8"))
    info = tenants.get(key)
    if not info:
        return None
    cfg = SCRIPT_DIR / info["config"]
    return cfg if cfg.exists() else None  # devolve Path (servidor.py le do arquivo)


# ── API publica ──────────────────────────────────────────────────────────────

def resolve(key: str):
    """key do aluno -> config (dict Lovable OU Path arquivo), ou None."""
    if not key:
        return None
    if _modo_lovable():
        return _resolver_lovable(key)
    return _resolver_arquivo(key)


def modo():
    return "lovable-api" if _modo_lovable() else "arquivo (tenants.json)"

"""
TENANT STORE — resolve a chave do aluno (mc_live_...) para o config dele.

Dois modos (escolhido por variavel de ambiente):
  1. SUPABASE  — se SUPABASE_URL e SUPABASE_SERVICE_KEY estiverem definidos:
                 busca o token do aluno na tabela ml_conexoes pela coluna mcp_key.
                 Renovacao de token escreve de volta no Supabase.
  2. ARQUIVO   — fallback: usa tenants.json (key -> config.json local).
                 Bom pra testar sem nuvem.

Interface:
  resolve(key) -> config (dict OU Path) pronto pro servidor.py, ou None se invalida.
"""

import os
import json
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
TENANTS_FILE = SCRIPT_DIR / "tenants.json"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ML_TABLE = os.environ.get("ML_CONEXOES_TABLE", "ml_conexoes")
# Coluna da chave do aluno. Lovable criou como "chave_mcp" (nao "mcp_key").
KEY_COLUMN = os.environ.get("MCP_KEY_COLUMN", "chave_mcp")


def _modo_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


# ── Modo Supabase ────────────────────────────────────────────────────────────

def _supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _resolver_supabase(key: str):
    """Busca a linha do aluno por mcp_key e devolve um config-dict com _save."""
    url = f"{SUPABASE_URL}/rest/v1/{ML_TABLE}"
    r = requests.get(url, headers=_supabase_headers(),
                     params={KEY_COLUMN: f"eq.{key}", "select": "*", "limit": "1"},
                     timeout=10)
    if r.status_code != 200 or not r.json():
        return None
    row = r.json()[0]

    # Mapeia colunas da tabela -> formato que o servidor.py espera
    config = {
        "access_token": row.get("access_token"),
        "refresh_token": row.get("refresh_token"),
        "expires_at": row.get("expires_at"),
        "seller_id": str(row.get("seller_id") or ""),
        "advertiser_id": str(row.get("advertiser_id") or row.get("seller_id") or ""),
    }

    # Callback de renovacao: grava o token novo de volta no Supabase
    def _save(novo):
        requests.patch(
            url, headers=_supabase_headers(),
            params={KEY_COLUMN: f"eq.{key}"},
            json={
                "access_token": novo.get("access_token"),
                "refresh_token": novo.get("refresh_token"),
                "expires_at": novo.get("expires_at"),
            }, timeout=10,
        )

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
    """key do aluno -> config (dict Supabase OU Path arquivo), ou None."""
    if not key:
        return None
    if _modo_supabase():
        return _resolver_supabase(key)
    return _resolver_arquivo(key)


def modo():
    return "supabase" if _modo_supabase() else "arquivo (tenants.json)"

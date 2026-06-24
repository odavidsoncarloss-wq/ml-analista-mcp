"""
ML ANALISTA — Servidor MCP HTTP (multi-inquilino)
=================================================
Roda o MESMO servidor.py (23 tools) como MCP remoto via HTTP.
Cada aluno usa uma URL com sua chave:

    https://SEU_DOMINIO/mcp?key=mc_live_XXXX

O middleware lê a ?key=, descobre QUAL aluno é (tenants.json) e aponta o
_token() pro config.json daquele aluno. Zero instalação pro aluno: ele só
cola a URL no Claude Desktop (Conectores > Adicionar conector personalizado).

Rodar local pra testar:
    python servidor_http.py
    -> sobe em http://127.0.0.1:8000/mcp

Depois, em produção, é o mesmo arquivo num servidor na nuvem (Railway/VPS).
"""

import os
from pathlib import Path

import servidor  # reaproveita as 23 tools + _tenant_config + mcp
import tenant_store  # resolve a chave -> config (Supabase ou arquivo)
from starlette.responses import JSONResponse

SCRIPT_DIR = Path(__file__).parent


def resolver_config(key: str):
    """key do aluno -> config (dict Supabase OU Path arquivo), ou None."""
    return tenant_store.resolve(key)


# App ASGI do MCP (streamable HTTP) — expõe /mcp
mcp_app = servidor.mcp.streamable_http_app()


# ── Middleware: extrai a ?key= e define o inquilino da requisição ────────────
class TenantMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Lê ?key= da query string
        query = dict(
            p.split("=", 1) if "=" in p else (p, "")
            for p in scope.get("query_string", b"").decode().split("&") if p
        )
        key = query.get("key", "")

        # Healthcheck simples sem precisar de chave
        if scope["path"] in ("/", "/health"):
            return await JSONResponse({"status": "ok", "service": "ml-analista-mcp"})(scope, receive, send)

        cfg = resolver_config(key) if key else None
        if cfg is None:
            return await JSONResponse(
                {"erro": "chave invalida ou ausente", "dica": "use ?key=mc_live_..."},
                status_code=401,
            )(scope, receive, send)

        # Define o config do aluno SÓ para esta requisição (contextvar isolado)
        token = servidor._tenant_config.set(cfg)
        try:
            await self.app(scope, receive, send)
        finally:
            servidor._tenant_config.reset(token)


app = TenantMiddleware(mcp_app)


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("ML ANALISTA — MCP HTTP (multi-inquilino)")
    print("=" * 60)
    porta = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Modo de inquilinos: {tenant_store.modo()}")
    print(f"Local:  http://{host}:{porta}/mcp?key=mc_live_DEMO_neoshop_001")
    print(f"Health: http://{host}:{porta}/health")
    print("=" * 60)
    uvicorn.run(app, host=host, port=porta, log_level="info")

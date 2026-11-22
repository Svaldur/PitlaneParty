import asyncio
import json
import os
import hashlib
from datetime import datetime
from http import HTTPStatus
import websockets

DB_FILE = "database_pitlane.json"
DATABASE_URL = os.environ.get("DATABASE_URL")

try:
    import psycopg2
except ImportError:
    psycopg2 = None

def _obter_conexao_pg():
    return psycopg2.connect(DATABASE_URL)

def carregar_db():
    padrao = {"contas": {}, "equipas": {}, "pontuacoes": {}}
    if DATABASE_URL and psycopg2:
        try:
            conn = _obter_conexao_pg()
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS pitlane_store (id INT PRIMARY KEY, dados JSONB)")
            cur.execute("SELECT dados FROM pitlane_store WHERE id = 1")
            row = cur.fetchone()
            if row and row[0]:
                dados = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                dados.setdefault("contas", {})
                dados.setdefault("equipas", {})
                dados.setdefault("pontuacoes", {})
                cur.close()
                conn.close()
                return dados
            else:
                cur.execute("INSERT INTO pitlane_store (id, dados) VALUES (1, %s) ON CONFLICT (id) DO NOTHING", (json.dumps(padrao),))
                conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print("Erro ao carregar PostgreSQL:", e)
        return padrao

    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
            dados.setdefault("contas", {})
            dados.setdefault("equipas", {})
            dados.setdefault("pontuacoes", {})
            return dados
    return padrao

def guardar_db():
    if DATABASE_URL and psycopg2:
        try:
            conn = _obter_conexao_pg()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO pitlane_store (id, dados) VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET dados = EXCLUDED.dados",
                (json.dumps(DB, ensure_ascii=False),)
            )
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception as e:
            print("Erro ao guardar no PostgreSQL:", e)

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DB, f, indent=2, ensure_ascii=False)

def encriptar_senha(senha):
    salt = "pitlane_party_salt_2026"
    return hashlib.sha256((senha + salt).encode("utf-8")).hexdigest()

DB = carregar_db()

def obter_equipa_do_piloto(nome_piloto):
    for eq_nome, info in DB["equipas"].items():
        if nome_piloto in info["membros"]:
            return eq_nome
    return "Sem Equipa"

def gerar_leaderboards():
    tabelas = {}
    for periodo in ["diaria", "semanal", "mensal", "anual"]:
        lista = []
        for piloto, dados in DB["pontuacoes"].items():
            lista.append({
                "nome": piloto,
                "equipa": dados.get("equipa", "Sem Equipa"),
                "pontos": dados.get(periodo, 0),
                "voltas": dados.get("voltas", 0)
            })
        lista.sort(key=lambda x: x["pontos"], reverse=True)
        tabelas[periodo] = lista[:10]
    return tabelas

async def gerir_jogador(websocket):
    player_id = str(id(websocket))
    JOGADORES[player_id] = {
        "id": player_id,
        "autenticado": False,
        "email": "",
        "nome": f"Piloto_{player_id[-4:]}",
        "equipa": "Sem Equipa",
        "zona": "menu",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "ry": 0.0,
        "cor_fato": "cinza_claro",
        "cor_calcas": "cinza_escuro",
        "capacete": "nenhum"
    }
    print(f"[+] Ligação recebida: {player_id}")

    try:
        await websocket.send(json.dumps({
            "tipo": "init",
            "id": player_id,
            "chat": CHAT_HISTORICO[-10:],
            "equipas": DB["equipas"],
            "leaderboards": gerar_leaderboards()
        }))

        async for mensagem in websocket:
            dados = json.loads(mensagem)
            tipo = dados.get("tipo")

            if tipo == "registar":
                email = dados.get("email", "").strip().lower()
                senha = dados.get("senha", "").strip()
                nome = dados.get("nome", "").strip()[:16]

                if not email or "@" not in email or len(senha) < 4 or not nome:
                    await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": False, "msg": "Preenche e-mail válido, nome e senha (mín. 4 caracteres)."}))
                    continue
                if email in DB["contas"]:
                    await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": False, "msg": "Este e-mail já está registado!"}))
                    continue
                if any(c["nome"].lower() == nome.lower() for c in DB["contas"].values()):
                    await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": False, "msg": "Esse nome de piloto já está em uso!"}))
                    continue

                nova_conta = {
                    "senha_hash": encriptar_senha(senha),
                    "nome": nome,
                    "criado_em": datetime.now().strftime("%Y-%m-%d"),
                    "pitcoins": 150,
                    "cor_fato": "cinza_claro",
                    "cor_calcas": "cinza_escuro",
                    "capacete": "nenhum",
                    "inventario": ["cinza_claro", "nenhum"],
                    "upgrades": {"motor": 0, "pneus": 0, "travoes": 0}
                }
                DB["contas"][email] = nova_conta
                DB["pontuacoes"][nome] = {"equipa": "Sem Equipa", "diaria": 0, "semanal": 0, "mensal": 0, "anual": 0, "voltas": 0}
                guardar_db()

                JOGADORES[player_id].update({"autenticado": True, "email": email, "nome": nome})
                perfil_envio = {k: v for k, v in nova_conta.items() if k != "senha_hash"}
                perfil_envio["email"] = email
                perfil_envio["equipa"] = "Sem Equipa"
                perfil_envio["dias_jogo"] = 1
                await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": True, "perfil": perfil_envio}))
                continue

            elif tipo == "login":
                email = dados.get("email", "").strip().lower()
                senha = dados.get("senha", "").strip()
                conta = DB["contas"].get(email)

                if not conta or conta["senha_hash"] != encriptar_senha(senha):
                    await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": False, "msg": "E-mail ou senha incorretos."}))
                    continue

                nome = conta["nome"]
                eq_atual = obter_equipa_do_piloto(nome)
                data_criacao = conta.get("criado_em", datetime.now().strftime("%Y-%m-%d"))
                dias_jogo = max(1, (datetime.now() - datetime.strptime(data_criacao, "%Y-%m-%d")).days + 1)

                JOGADORES[player_id].update({
                    "autenticado": True,
                    "email": email,
                    "nome": nome,
                    "equipa": eq_atual,
                    "cor_fato": conta["cor_fato"],
                    "cor_calcas": conta["cor_calcas"],
                    "capacete": conta["capacete"]
                })
                perfil_envio = {k: v for k, v in conta.items() if k != "senha_hash"}
                perfil_envio["email"] = email
                perfil_envio["equipa"] = eq_atual
                perfil_envio["dias_jogo"] = dias_jogo
                await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": True, "perfil": perfil_envio}))
                continue

            elif tipo == "guardar_progresso":
                email = dados.get("email", "")
                if email in DB["contas"]:
                    DB["contas"][email].update({
                        "pitcoins": dados.get("pitcoins", DB["contas"][email]["pitcoins"]),
                        "cor_fato": dados.get("cor_fato", DB["contas"][email]["cor_fato"]),
                        "cor_calcas": dados.get("cor_calcas", DB["contas"][email]["cor_calcas"]),
                        "capacete": dados.get("capacete", DB["contas"][email]["capacete"]),
                        "inventario": dados.get("inventario", DB["contas"][email]["inventario"]),
                        "upgrades": dados.get("upgrades", DB["contas"][email]["upgrades"])
                    })
                    guardar_db()

            elif tipo == "trocar_senha":
                email = dados.get("email", "")
                senha_atual = dados.get("senha_atual", "")
                nova_senha = dados.get("nova_senha", "").strip()
                if email in DB["contas"]:
                    if DB["contas"][email]["senha_hash"] != encriptar_senha(senha_atual):
                        await websocket.send(json.dumps({"tipo": "senha_resp", "sucesso": False, "msg": "Senha atual incorreta."}))
                    elif len(nova_senha) < 4:
                        await websocket.send(json.dumps({"tipo": "senha_resp", "sucesso": False, "msg": "A nova senha deve ter pelo menos 4 caracteres."}))
                    else:
                        DB["contas"][email]["senha_hash"] = encriptar_senha(nova_senha)
                        guardar_db()
                        await websocket.send(json.dumps({"tipo": "senha_resp", "sucesso": True, "msg": "Senha alterada com sucesso!"}))
                continue

            elif tipo == "logout":
                JOGADORES[player_id].update({"autenticado": False, "email": "", "zona": "menu"})

            elif tipo == "update":
                nome_p = dados.get("nome", JOGADORES[player_id]["nome"])
                eq_atual = obter_equipa_do_piloto(nome_p)
                if nome_p in DB["pontuacoes"]:
                    DB["pontuacoes"][nome_p]["equipa"] = eq_atual
                JOGADORES[player_id].update({
                    "nome": nome_p,
                    "equipa": eq_atual,
                    "zona": dados.get("zona", "paddock"),
                    "x": dados["x"],
                    "y": dados["y"],
                    "z": dados["z"],
                    "ry": dados["ry"],
                    "progresso": dados.get("progresso", 0),
                    "cor_fato": dados.get("cor_fato", "cinza_claro"),
                    "cor_calcas": dados.get("cor_calcas", "cinza_escuro"),
                    "capacete": dados.get("capacete", "nenhum")
                })

            elif tipo == "chat":
                eq_tag = f"[{JOGADORES[player_id]['equipa']}] " if JOGADORES[player_id]['equipa'] != "Sem Equipa" else ""
                msg_texto = f"{eq_tag}{JOGADORES[player_id]['nome']}: {dados['texto']}"
                CHAT_HISTORICO.append(msg_texto)
                if len(CHAT_HISTORICO) > 30:
                    CHAT_HISTORICO.pop(0)

            elif tipo == "criar_equipa":
                nome_p = dados.get("nome", "")
                nome_eq = dados.get("nome_equipa", "").strip()[:18]
                if nome_eq and nome_eq not in DB["equipas"] and obter_equipa_do_piloto(nome_p) == "Sem Equipa":
                    DB["equipas"][nome_eq] = {"lider": nome_p, "membros": [nome_p], "cor": "vermelho"}
                    guardar_db()

            elif tipo == "entrar_equipa":
                nome_p = dados.get("nome", "")
                nome_eq = dados.get("nome_equipa", "")
                if nome_eq in DB["equipas"] and obter_equipa_do_piloto(nome_p) == "Sem Equipa":
                    if len(DB["equipas"][nome_eq]["membros"]) < 5:
                        DB["equipas"][nome_eq]["membros"].append(nome_p)
                        guardar_db()

            elif tipo == "sair_equipa":
                nome_p = dados.get("nome", "")
                eq_atual = obter_equipa_do_piloto(nome_p)
                if eq_atual in DB["equipas"]:
                    DB["equipas"][eq_atual]["membros"].remove(nome_p)
                    if len(DB["equipas"][eq_atual]["membros"]) == 0:
                        del DB["equipas"][eq_atual]
                    guardar_db()

            elif tipo == "ganhar_pontos":
                nome_p = dados.get("nome", "")
                pts = int(dados.get("pontos", 25))
                voltas_ganhas = int(dados.get("voltas", 1))
                eq_atual = obter_equipa_do_piloto(nome_p)
                if nome_p not in DB["pontuacoes"]:
                    DB["pontuacoes"][nome_p] = {"equipa": eq_atual, "diaria": 0, "semanal": 0, "mensal": 0, "anual": 0, "voltas": 0}
                DB["pontuacoes"][nome_p].setdefault("voltas", 0)
                DB["pontuacoes"][nome_p]["voltas"] += voltas_ganhas
                for p_chave in ["diaria", "semanal", "mensal", "anual"]:
                    DB["pontuacoes"][nome_p][p_chave] += pts
                guardar_db()

            estado = {
                "tipo": "estado",
                "jogadores": JOGADORES,
                "chat": CHAT_HISTORICO[-8:],
                "equipas": DB["equipas"],
                "leaderboards": gerar_leaderboards()
            }
            await websocket.send(json.dumps(estado))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        print(f"[-] Ligação encerrada: {player_id}")
        JOGADORES.pop(player_id, None)

import os
# ... (no final do server.py)
from http import HTTPStatus

async def health_check(connection, request):
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return connection.respond(HTTPStatus.OK, "Pitlane Party 3D Server Online!\n")

async def main():
    porta = int(os.environ.get("PORT", 8765))
    print(f"Servidor Pitlane Party 3D ativo na porta {porta}")
    async with websockets.serve(gerir_jogador, "0.0.0.0", porta, process_request=health_check):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())


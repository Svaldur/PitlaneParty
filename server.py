import asyncio
import json
import os
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from http import HTTPStatus
import websockets

DB_FILE = "database_pitlane.json"
DATABASE_URL = os.environ.get("DATABASE_URL")

try:
    import psycopg2
except ImportError:
    psycopg2 = None

def _obter_conexao_pg():
    url = DATABASE_URL
    if url and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return psycopg2.connect(url)

def carregar_db():
    padrao = {"contas": {}, "equipas": {}, "pontuacoes": {}}
    if DATABASE_URL and psycopg2:
        try:
            conn = _obter_conexao_pg()
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS pitlane_store (id INT PRIMARY KEY, dados TEXT)")
            cur.execute("SELECT dados FROM pitlane_store WHERE id = 1")
            row = cur.fetchone()
            if row:
                val = next(iter(row))
                dados = val if isinstance(val, dict) else json.loads(val)
                dados.setdefault("contas", {})
                dados.setdefault("equipas", {})
                dados.setdefault("pontuacoes", {})
                dados.setdefault("tempos", {"classic": {"voltas": [], "corridas": []}, "monza": {"voltas": [], "corridas": []}, "portimao": {"voltas": [], "corridas": []}})
                dados.setdefault("reset_meta", {})
                cur.close()
                conn.close()
                print("[DB] Dados carregados do Neon PostgreSQL!")
                return dados
            else:
                cur.execute("INSERT INTO pitlane_store (id, dados) VALUES (1, %s) ON CONFLICT (id) DO NOTHING", (json.dumps(padrao),))
                conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print("[DB] Erro ao carregar PostgreSQL:", e)
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
            cur.execute("CREATE TABLE IF NOT EXISTS pitlane_store (id INT PRIMARY KEY, dados TEXT)")
            cur.execute(
                "INSERT INTO pitlane_store (id, dados) VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET dados = EXCLUDED.dados",
                (json.dumps(DB, ensure_ascii=False),)
            )
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception as e:
            print("[DB] Erro ao guardar no PostgreSQL:", e)

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DB, f, indent=2, ensure_ascii=False)

def encriptar_senha(senha):
    salt = "pitlane_party_salt_2026"
    return hashlib.sha256((senha + salt).encode("utf-8")).hexdigest()

DB = carregar_db()
JOGADORES = {}
CHAT_HISTORICO = []

def obter_equipa_do_piloto(nome_piloto):
    for eq_nome, info in DB["equipas"].items():
        if nome_piloto in info["membros"]:
            return eq_nome
    return "Sem Equipa"


def verificar_resets_lisboa():
    agora = datetime.now(ZoneInfo("Europe/Lisbon"))
    dia_id = agora.strftime("%Y-%m-%d")
    sem_id = (agora.date()).toordinal() - ((agora.weekday() + 1) % 7)
    mes_id = agora.strftime("%Y-%m")
    ano_id = agora.strftime("%Y")
    meta = DB.setdefault("reset_meta", {"dia": dia_id, "semana": sem_id, "mes": mes_id, "ano": ano_id})
    alt = False
    if meta.get("dia") != dia_id:
        for d in DB["pontuacoes"].values(): d["diaria"] = 0
        meta["dia"], alt = dia_id, True
    if meta.get("semana") != sem_id:
        for d in DB["pontuacoes"].values(): d["semanal"] = 0
        meta["semana"], alt = sem_id, True
    if meta.get("mes") != mes_id:
        for d in DB["pontuacoes"].values(): d["mensal"] = 0
        meta["mes"], alt = mes_id, True
    if meta.get("ano") != ano_id:
        for d in DB["pontuacoes"].values(): d["anual"] = 0
        meta["ano"], alt = ano_id, True
    if alt: guardar_db()

def gerar_leaderboards():
    verificar_resets_lisboa()
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
        "progresso": 0.0,
        "cor_fato": "cinza_claro",
        "cor_calcas": "cinza_escuro",
        "capacete": "nenhum"
    }
    print(f"[+] Ligacao recebida: {player_id}")

    try:
        await websocket.send(json.dumps({
            "tipo": "init",
            "id": player_id,
            "chat": CHAT_HISTORICO[-10:],
            "equipas": DB["equipas"],
            "leaderboards": gerar_leaderboards(), "tempos": DB.setdefault("tempos", {"classic": {"voltas": [], "corridas": []}, "monza": {"voltas": [], "corridas": []}, "portimao": {"voltas": [], "corridas": []}})
        }))

        async for mensagem in websocket:
            dados = json.loads(mensagem)
            tipo = dados.get("tipo")

            if tipo == "registar":
                email = dados.get("email", "").strip().lower()
                senha = dados.get("senha", "").strip()
                nome = dados.get("nome", "").strip()[:16]

                if not email or "@" not in email or len(senha) < 4 or not nome:
                    await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": False, "msg": "Preenche e-mail valido, nome e senha (min. 4 caracteres)."}))
                    continue
                if email in DB["contas"]:
                    await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": False, "msg": "Este e-mail ja esta registado!"}))
                    continue
                if any(c["nome"].lower() == nome.lower() for c in DB["contas"].values()):
                    await websocket.send(json.dumps({"tipo": "auth_resp", "sucesso": False, "msg": "Esse nome de piloto ja esta em uso!"}))
                    continue

                nova_conta = {
                    "senha_hash": encriptar_senha(senha),
                    "nome": nome,
                    "criado_em": datetime.now().strftime("%Y-%m-%d"),
                    "pitcoins": 150,
                    "pontos": 0,
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
                perfil_envio["pontos"] = conta.get("pontos", DB["pontuacoes"].get(nome, {}).get("anual", 0))
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
                    "progresso": dados.get("progresso", 0.0),
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
            elif tipo == "convidar_equipa":
                remetente = dados.get("remetente", "")
                alvo_nome = dados.get("alvo_nome", "").strip()
                eq_atual = obter_equipa_do_piloto(remetente)
                if eq_atual != "Sem Equipa" and len(DB["equipas"][eq_atual]["membros"]) < 5:
                    for pid_alvo, info_alvo in JOGADORES.items():
                        if info_alvo.get("nome", "").lower() == alvo_nome.lower() and info_alvo.get("zona") == "paddock":
                            if obter_equipa_do_piloto(info_alvo["nome"]) == "Sem Equipa":
                                info_alvo["convite_pendente"] = {"de": remetente, "equipa": eq_atual}
                            break

            elif tipo == "responder_convite_equipa":
                nome_p = dados.get("nome", "")
                aceitar = dados.get("aceitar", False)
                eq_convite = dados.get("equipa", "")
                JOGADORES[player_id].pop("convite_pendente", None)
                if aceitar and eq_convite in DB["equipas"] and obter_equipa_do_piloto(nome_p) == "Sem Equipa":
                    if len(DB["equipas"][eq_convite]["membros"]) < 5:
                        DB["equipas"][eq_convite]["membros"].append(nome_p)
                        guardar_db()
            elif tipo == "convidar_corrida":
                remetente = dados.get("remetente", "")
                alvo_nome = dados.get("alvo_nome", "").strip()
                cfg = dados.get("cfg", {"pista": "Circuito Clássico", "voltas": 3, "bots": True})
                for pid_alvo, info_alvo in JOGADORES.items():
                    if info_alvo.get("nome", "").lower() == alvo_nome.lower() and info_alvo.get("zona") == "paddock":
                        info_alvo["convite_corrida"] = {"de": remetente, "cfg": cfg}
                        break

            elif tipo == "responder_convite_corrida":
                JOGADORES[player_id].pop("convite_corrida", None)

            elif tipo == "registar_tempo":
                pista_k, cat_k = dados.get("pista", "classic"), dados.get("categoria", "voltas")
                nome_p, eq_p = dados.get("nome", "Piloto"), dados.get("equipa", "Sem Equipa")
                tempo_v, voltas_c = float(dados.get("tempo", 9999.0)), int(dados.get("voltas", 1))
                t_root = DB.setdefault("tempos", {"classic": {"voltas": [], "corridas": []}, "monza": {"voltas": [], "corridas": []}, "portimao": {"voltas": [], "corridas": []}})
                lista_t = t_root.setdefault(pista_k, {"voltas": [], "corridas": []}).setdefault(cat_k, [])
                ex = next((x for x in lista_t if x["nome"] == nome_p and (cat_k == "voltas" or x.get("voltas", 3) == voltas_c)), None)
                if ex:
                    if int(tempo_v * 1000) // max(1, int(ex["tempo"] * 1000)) == 0:
                        ex["tempo"], ex["equipa"] = tempo_v, eq_p
                else:
                    lista_t.append({"nome": nome_p, "equipa": eq_p, "tempo": tempo_v, "voltas": voltas_c})
                lista_t.sort(key=lambda x: x["tempo"])
                del lista_t[15:]
                guardar_db()
            elif tipo == "convidar_amigo":
                rem, alvo_n = dados.get("de", ""), dados.get("para", "")
                for pid, info in JOGADORES.items():
                    if info.get("nome") == alvo_n and info.get("zona") == "paddock":
                        info["pedido_amizade"] = rem
                        break
            elif tipo == "responder_amizade":
                eu_n, de_n, ac = dados.get("nome", ""), dados.get("de", ""), dados.get("aceitar", False)
                JOGADORES[player_id].pop("pedido_amizade", None)
                if ac:
                    for c in DB["contas"].values():
                        if c["nome"] == eu_n and de_n not in c.setdefault("amigos", []):
                            c["amigos"].append(de_n)
                        elif c["nome"] == de_n and eu_n not in c.setdefault("amigos", []):
                            c["amigos"].append(eu_n)
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
                "leaderboards": gerar_leaderboards(), "tempos": DB.setdefault("tempos", {"classic": {"voltas": [], "corridas": []}, "monza": {"voltas": [], "corridas": []}, "portimao": {"voltas": [], "corridas": []}})
            }
            await websocket.send(json.dumps(estado))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        print(f"[-] Ligacao encerrada: {player_id}")
        JOGADORES.pop(player_id, None)

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

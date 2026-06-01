# cliente.py — Cliente Analitico da Cidade Inteligente
# Conecta-se ao Gateway via TCP e envia comandos empacotados em Protobuf.

import socket
from protos import todolist_pb2

# --- Configuracao de Rede ---
GATEWAY_IP       = '127.0.0.1'
GATEWAY_TCP_PORT = 5009


# ---------------------------------------------------------------------------
# Camada de comunicacao
# ---------------------------------------------------------------------------

def enviar_comando(req):
    """Abre uma conexao TCP com o Gateway, envia um ClientRequest Protobuf
    e retorna a string 'message' da GatewayResponse.

    Retorna None se o Gateway estiver inacessivel.
    """
    envelope = todolist_pb2.SmartCityMessage(client_request=req)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((GATEWAY_IP, GATEWAY_TCP_PORT))
            s.sendall(envelope.SerializeToString())

            raw = s.recv(4096)
            if not raw:
                print("[ERRO] Gateway nao enviou resposta.")
                return None

            resp = todolist_pb2.SmartCityMessage()
            resp.ParseFromString(raw)
            return resp.gateway_response.message

    except ConnectionRefusedError:
        print(f"[ERRO] Gateway em {GATEWAY_IP}:{GATEWAY_TCP_PORT} nao esta acessivel.")
        print("       Verifique se o gateway.py esta rodando.")
        return None
    except socket.timeout:
        print("[ERRO] Timeout: o Gateway nao respondeu em 5 segundos.")
        return None
    except Exception as e:
        print(f"[ERRO] Falha na comunicacao com o Gateway: {e}")
        return None


# ---------------------------------------------------------------------------
# Acoes do menu (cada uma monta e envia o ClientRequest correto)
# ---------------------------------------------------------------------------

def acao_listar_dispositivos():
    req = todolist_pb2.ClientRequest(command="LIST_DEVICES")
    resultado = enviar_comando(req)
    if resultado:
        print("\n" + resultado)


def acao_consultar_media():
    try:
        device_id = input("  Digite o ID do sensor: ").strip()
        if not device_id:
            print("  ID nao pode ser vazio.")
            return

        req = todolist_pb2.ClientRequest(
            command="GET_AVG",
            target_device_id=device_id
        )
        resultado = enviar_comando(req)
        if resultado:
            print(f"\n  {resultado}")
    except (KeyboardInterrupt, EOFError):
        pass


def acao_set_state():
    try:
        device_id = input("  Digite o ID do atuador: ").strip()
        if not device_id:
            print("  ID nao pode ser vazio.")
            return

        estado_str = input("  Novo estado [1=Ligar / 0=Desligar]: ").strip()
        if estado_str not in ("0", "1"):
            print("  Opcao invalida. Digite 1 para Ligar ou 0 para Desligar.")
            return

        new_state = estado_str == "1"
        req = todolist_pb2.ClientRequest(
            command="SET_STATE",
            target_device_id=device_id,
            new_state=new_state
        )
        resultado = enviar_comando(req)
        if resultado:
            print(f"\n  {resultado}")
    except (KeyboardInterrupt, EOFError):
        pass


# ---------------------------------------------------------------------------
# Menu principal
# ---------------------------------------------------------------------------

MENU = """
============================================
   Cliente Analitico — Cidade Inteligente
============================================
  [1] Listar dispositivos online
  [2] Consultar media de um sensor
  [3] Ligar / Desligar atuador
  [0] Sair
--------------------------------------------"""


def main():
    print(MENU)
    while True:
        try:
            opcao = input("Escolha uma opcao: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nEncerrando cliente.")
            break

        if opcao == "1":
            acao_listar_dispositivos()

        elif opcao == "2":
            acao_consultar_media()

        elif opcao == "3":
            acao_set_state()

        elif opcao == "0":
            print("Encerrando cliente. Ate logo!")
            break

        else:
            print("  Opcao invalida. Digite 1, 2, 3 ou 0.")

        print(MENU)


if __name__ == "__main__":
    main()

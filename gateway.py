"""Gateway Inteligente — Cidade Inteligente (SD)
Responsabilidades:
  - Descoberta de dispositivos via UDP Multicast (broadcaster puro, sem recvfrom)
  - Recepção unificada de anúncios e dados de sensores na porta GATEWAY_DATA_PORT
  - (Próximo passo) Servidor TCP para o Cliente Analítico
"""

import socket
import threading
import time

from protos import todolist_pb2

# --- Configurações de Rede ---
MULTICAST_GROUP   = '224.1.1.1'
MULTICAST_PORT    = 5007
GATEWAY_DATA_PORT = 5008   # Porta UDP unificada: anúncios + dados de sensores
DISCOVERY_INTERVAL = 5     # Segundos entre cada broadcast de descoberta


# --- Estado compartilhado (thread-safe com lock) ---
dispositivos_conectados = {}   # {device_id: dict com info do dispositivo}
historico_leituras      = {}   # {device_id: [(timestamp, value, unit), ...]}
lock = threading.Lock()


# ---------------------------------------------------------------------------
# BROADCASTER DE DESCOBERTA (Multicast UDP — envia apenas, nunca recebe)
# ---------------------------------------------------------------------------

def thread_multicast_discovery():
    """Broadcaster puro: anuncia o Gateway via Multicast a cada DISCOVERY_INTERVAL segundos.

    Responsabilidade única: enviar a mensagem Protobuf com a porta de dados.
    NÃO faz recvfrom — o recebimento de anúncios é feito por thread_ouvir_udp().

    Isso permite que novos dispositivos se conectem a qualquer momento sem
    depender de uma janela de escuta acoplada ao envio.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

    # Monta a mensagem uma única vez (imutável durante a vida do Gateway)
    discovery_msg = todolist_pb2.SmartCityMessage()
    # [CORREÇÃO] Campo renomeado de gateway_discovery → discovery no .proto
    discovery_msg.discovery.data_port = GATEWAY_DATA_PORT
    serialized = discovery_msg.SerializeToString()

    print(f"[Gateway-Discovery] Broadcaster iniciado → {MULTICAST_GROUP}:{MULTICAST_PORT}")
    print(f"[Gateway-Discovery] Dispositivos devem enviar anúncios/dados para a porta {GATEWAY_DATA_PORT}")

    while True:
        sock.sendto(serialized, (MULTICAST_GROUP, MULTICAST_PORT))
        print(f"[Gateway-Discovery] Mensagem de descoberta enviada (Protobuf, porta={GATEWAY_DATA_PORT}).")
        # Aguarda antes do próximo broadcast — não bloqueia outras threads
        time.sleep(DISCOVERY_INTERVAL)


# ---------------------------------------------------------------------------
# RECEPTOR UNIFICADO UDP (anúncios de dispositivos + dados de sensores)
# ---------------------------------------------------------------------------

def thread_ouvir_udp():
    """Escuta na porta GATEWAY_DATA_PORT qualquer mensagem UDP Protobuf.

    Trata dois tipos de payload:
      • announcement → novo dispositivo se registrando
      • sensor_data  → leitura periódica de um sensor
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', GATEWAY_DATA_PORT))
    print(f"[Gateway-UDP] Aguardando mensagens na porta {GATEWAY_DATA_PORT} (anúncios + dados)")

    while True:
        data, addr = sock.recvfrom(4096)

        try:
            msg = todolist_pb2.SmartCityMessage()
            msg.ParseFromString(data)

            if msg.HasField("announcement"):
                _registrar_dispositivo(msg.announcement)

            elif msg.HasField("sensor_data"):
                _armazenar_leitura(msg.sensor_data)

        except Exception as e:
            print(f"[Gateway-UDP] Erro ao processar pacote de {addr}: {e}")


def _registrar_dispositivo(info):
    """Registra um novo dispositivo ou atualiza o último contato."""
    with lock:
        if info.device_id in dispositivos_conectados:
            dispositivos_conectados[info.device_id]["ultimo_contato"] = time.time()
            return

        dispositivos_conectados[info.device_id] = {
            "id":             info.device_id,
            "type":           todolist_pb2.DeviceType.Name(info.type),
            "ip":             info.ip_address,
            "port":           info.port,
            "is_actuator":    info.is_actuator,
            "ativo":          True,
            "ultimo_contato": time.time(),
        }
        historico_leituras[info.device_id] = []

    print(f"\n[Gateway-UDP] ✓ Dispositivo registrado:")
    print(f"             ID:      {info.device_id}")
    print(f"             Tipo:    {todolist_pb2.DeviceType.Name(info.type)}")
    print(f"             Atuador: {info.is_actuator}\n")


def _armazenar_leitura(sd):
    """Adiciona uma leitura de sensor ao histórico."""
    with lock:
        if sd.device_id not in historico_leituras:
            historico_leituras[sd.device_id] = []
        historico_leituras[sd.device_id].append(
            (sd.timestamp, sd.value, sd.unit)
        )
        if sd.device_id in dispositivos_conectados:
            dispositivos_conectados[sd.device_id]["ultimo_contato"] = time.time()

    print(f"[Gateway-UDP] 📡 Leitura de '{sd.device_id}': {sd.value:.2f} {sd.unit}")


# ---------------------------------------------------------------------------
# PONTO DE ENTRADA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 52)
    print("   Gateway Inteligente — Cidade Inteligente")
    print("=" * 52)

    # Broadcaster Multicast: envia descoberta a cada 5 s (daemon — morre com o processo)
    threading.Thread(target=thread_multicast_discovery, daemon=True).start()

    # Receptor UDP unificado: anúncios + dados de sensores na mesma porta
    threading.Thread(target=thread_ouvir_udp, daemon=True).start()

    # (Próximo passo) Servidor TCP para o Cliente Analítico
    # threading.Thread(target=thread_servidor_tcp, daemon=True).start()

    print("[Gateway] Em execução. Pressione Ctrl+C para sair.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Gateway] Desligando.")

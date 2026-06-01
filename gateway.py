"""Gateway Inteligente — Cidade Inteligente (SD)
Responsabilidades:
  - Descoberta de dispositivos via UDP Multicast (mensagem Protobuf GatewayDiscovery)
  - Registro contínuo de dispositivos que respondem ao Multicast
  - Recepção de dados de sensores via UDP (porta GATEWAY_DATA_PORT)
  - (Próximo passo) Servidor TCP para o Cliente Analítico
"""

import socket
import struct
import threading
import time

from protos import todolist_pb2

# --- Configurações de Rede ---
MULTICAST_GROUP = '224.1.1.1'
MULTICAST_PORT = 5007
GATEWAY_DATA_PORT = 5008   # Porta UDP onde o Gateway escuta dados dos sensores
DISCOVERY_INTERVAL = 10    # Segundos entre cada rodada de descoberta Multicast

# --- Estado compartilhado (thread-safe com lock) ---
dispositivos_conectados = {}          # {device_id: dict com info do dispositivo}
historico_leituras = {}               # {device_id: [(timestamp, value, unit), ...]}
lock = threading.Lock()


# ---------------------------------------------------------------------------
# DESCOBERTA (Multicast UDP)
# ---------------------------------------------------------------------------

def thread_multicast_discovery():
    """Envia periodicamente a mensagem de descoberta Multicast em Protobuf
    e coleta os anúncios de resposta dos dispositivos no mesmo socket.

    MUDANÇA em relação ao test_gateway.py:
    - Antes: enviava bytes puros b'GATEWAY_DISCOVERY'
    - Agora:  envia SmartCityMessage com gateway_discovery.data_port serializado
    - Roda em loop contínuo (antes só descobria 1 dispositivo e encerrava)
    """
    # Socket de envio/recepção de descoberta (porta efêmera alocada pelo SO)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(1.0)  # Timeout curto para não bloquear o loop de coleta

    # [MUDANÇA] Monta a mensagem de descoberta em Protobuf uma única vez
    discovery_msg = todolist_pb2.SmartCityMessage()
    discovery_msg.gateway_discovery.data_port = GATEWAY_DATA_PORT
    serialized_discovery = discovery_msg.SerializeToString()

    print(f"[Gateway] Descoberta Multicast iniciada → grupo {MULTICAST_GROUP}:{MULTICAST_PORT}")
    print(f"[Gateway] Dispositivos devem enviar dados para a porta UDP {GATEWAY_DATA_PORT}")

    while True:
        # [MUDANÇA] Envia Protobuf serializado, não bytes puros
        sock.sendto(serialized_discovery, (MULTICAST_GROUP, MULTICAST_PORT))
        print(f"[Gateway] Mensagem de descoberta Multicast enviada (Protobuf).")

        # Janela de escuta: coleta anúncios por DISCOVERY_INTERVAL segundos
        # [MUDANÇA] Loop contínuo — antes era um único recvfrom com timeout de 5s
        deadline = time.time() + DISCOVERY_INTERVAL
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
                _processar_anuncio(data, addr)
            except socket.timeout:
                pass  # Normal — continua ouvindo até o deadline


def _processar_anuncio(data: bytes, addr: tuple):
    """Desserializa e registra o anúncio de um dispositivo."""
    try:
        msg = todolist_pb2.SmartCityMessage()
        msg.ParseFromString(data)

        if not msg.HasField("announcement"):
            return  # Ignora mensagens que não sejam anúncios

        info = msg.announcement
        with lock:
            if info.device_id in dispositivos_conectados:
                # Apenas atualiza o timestamp de último contato
                dispositivos_conectados[info.device_id]["ultimo_contato"] = time.time()
                return

            # Novo dispositivo — registra no estado global
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

        print(f"\n[Gateway] ✓ Dispositivo registrado:")
        print(f"          ID:       {info.device_id}")
        print(f"          Tipo:     {todolist_pb2.DeviceType.Name(info.type)}")
        print(f"          Endereço: {info.ip_address}:{info.port}")
        print(f"          Atuador:  {info.is_actuator}\n")

    except Exception as e:
        print(f"[Gateway] Erro ao processar anúncio de {addr}: {e}")


# ---------------------------------------------------------------------------
# RECEPÇÃO DE DADOS DE SENSORES (UDP)
# ---------------------------------------------------------------------------

def thread_ouvir_sensores():
    """Escuta dados UDP enviados pelos sensores na porta GATEWAY_DATA_PORT."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', GATEWAY_DATA_PORT))
    print(f"[Gateway] Aguardando dados de sensores na porta UDP {GATEWAY_DATA_PORT}")

    while True:
        data, addr = sock.recvfrom(2048)
        try:
            msg = todolist_pb2.SmartCityMessage()
            msg.ParseFromString(data)

            if msg.HasField("sensor_data"):
                sd = msg.sensor_data
                timestamp = time.time()

                with lock:
                    if sd.device_id in historico_leituras:
                        historico_leituras[sd.device_id].append(
                            (timestamp, sd.value, sd.unit)
                        )
                    # Atualiza "ultimo_contato" para detecção de falhas futura
                    if sd.device_id in dispositivos_conectados:
                        dispositivos_conectados[sd.device_id]["ultimo_contato"] = timestamp

                print(f"[Gateway] 📡 Dado de '{sd.device_id}': {sd.value:.2f} {sd.unit}")

        except Exception as e:
            print(f"[Gateway] Erro ao processar dado de sensor de {addr}: {e}")


# ---------------------------------------------------------------------------
# PONTO DE ENTRADA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 50)
    print("   Gateway Inteligente — Cidade Inteligente")
    print("=" * 50)

    # Thread de descoberta Multicast (envia Protobuf + coleta anúncios em loop)
    t_discovery = threading.Thread(target=thread_multicast_discovery, daemon=True)
    t_discovery.start()

    # Thread para receber dados contínuos dos sensores via UDP
    t_sensores = threading.Thread(target=thread_ouvir_sensores, daemon=True)
    t_sensores.start()

    # (Próximo passo) Thread do servidor TCP para o Cliente Analítico
    # t_tcp = threading.Thread(target=thread_servidor_tcp, daemon=True)
    # t_tcp.start()

    print("[Gateway] Em execução. Pressione Ctrl+C para sair.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Gateway] Desligando.")

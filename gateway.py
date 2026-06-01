"""Gateway Inteligente — Cidade Inteligente (SD)

Responsabilidades:
  - Descoberta de dispositivos via UDP Multicast (broadcaster puro)
  - Recepção unificada de anúncios e leituras na porta GATEWAY_DATA_PORT
  - Gestão de estado em memória: active_devices e sensor_history (thread-safe)
  - (Próximo passo) Servidor TCP para o Cliente Analítico
"""

import socket
import threading
import time

from protos import todolist_pb2

# --- Configurações de Rede (globais, imutáveis) ---
MULTICAST_GROUP    = '224.1.1.1'
MULTICAST_PORT     = 5007
GATEWAY_DATA_PORT  = 5008   # Porta UDP unificada: anúncios + dados de sensores
GATEWAY_TCP_PORT   = 5009   # Porta TCP exclusiva para o Cliente Analítico
DISCOVERY_INTERVAL = 5      # Segundos entre cada broadcast de descoberta Multicast


# ===========================================================================
class SmartCityGateway:
    """Cérebro do sistema: mantém o estado da cidade e coordena as threads."""

    def __init__(self):
        # -----------------------------------------------------------------
        # 1. ESTRUTURAS DE ESTADO EM MEMÓRIA
        # -----------------------------------------------------------------

        # Dicionário de dispositivos online.
        # Chave: device_id (str)
        # Valor: dict com tipo, IP, porta, flags e último contato
        self.active_devices = {}

        # Lista plana de todas as leituras recebidas de qualquer sensor.
        # Cada entrada é um dict com device_id, value, unit e timestamp.
        self.sensor_history = []

        # Lock único para toda operação de leitura/escrita nas duas estruturas
        # acima — evita Race Conditions entre as threads de descoberta e UDP.
        self.lock = threading.Lock()

    # -----------------------------------------------------------------------
    # 2. BROADCASTER DE DESCOBERTA (Multicast UDP — envia apenas, nunca recebe)
    # -----------------------------------------------------------------------

    def _thread_multicast_discovery(self):
        """Broadcaster puro: envia a mensagem Protobuf a cada DISCOVERY_INTERVAL s.

        Responsabilidade única: anunciar a porta de dados do Gateway para
        que novos dispositivos possam se conectar a qualquer momento.
        Não faz recvfrom — a recepção fica em _thread_ouvir_udp().
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

        # Monta a mensagem uma única vez (conteúdo não muda durante a execução)
        msg = todolist_pb2.SmartCityMessage()
        msg.discovery.data_port = GATEWAY_DATA_PORT
        serialized = msg.SerializeToString()

        print(f"[Discovery] Broadcaster iniciado → {MULTICAST_GROUP}:{MULTICAST_PORT}")
        print(f"[Discovery] Dispositivos devem responder na porta UDP {GATEWAY_DATA_PORT}")

        while True:
            sock.sendto(serialized, (MULTICAST_GROUP, MULTICAST_PORT))
            print(f"[Discovery] Mensagem de descoberta enviada (data_port={GATEWAY_DATA_PORT}).")
            time.sleep(DISCOVERY_INTERVAL)

    # -----------------------------------------------------------------------
    # 3. RECEPTOR UNIFICADO UDP (anúncios + leituras de sensores)
    # -----------------------------------------------------------------------

    def _thread_ouvir_udp(self):
        """Escuta na porta GATEWAY_DATA_PORT todo pacote UDP Protobuf.

        Roteia cada mensagem para o handler correto:
          • announcement → _registrar_dispositivo()
          • sensor_data  → _armazenar_leitura()
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('', GATEWAY_DATA_PORT))
        print(f"[UDP] Aguardando mensagens na porta {GATEWAY_DATA_PORT} (anúncios + dados)")

        while True:
            data, addr = sock.recvfrom(4096)
            try:
                msg = todolist_pb2.SmartCityMessage()
                msg.ParseFromString(data)

                if msg.HasField("announcement"):
                    self._registrar_dispositivo(msg.announcement)

                elif msg.HasField("sensor_data"):
                    self._armazenar_leitura(msg.sensor_data)

            except Exception as e:
                print(f"[UDP] Erro ao processar pacote de {addr}: {e}")

    # -----------------------------------------------------------------------
    # Handlers internos (chamados dentro das threads, protegidos pelo lock)
    # -----------------------------------------------------------------------

    def _registrar_dispositivo(self, announcement):
        """Salva ou atualiza um dispositivo em self.active_devices (thread-safe)."""
        # 2. ATUALIZAR O REGISTRO NA DESCOBERTA
        with self.lock:
            self.active_devices[announcement.device_id] = {
                'type':        todolist_pb2.DeviceType.Name(announcement.type),
                'ip':          announcement.ip_address,
                'port':        announcement.port,
                'is_actuator': announcement.is_actuator,
                'last_seen':   time.time()
            }

        print(f"\n[UDP] + Dispositivo registrado: {announcement.device_id}")
        print(f"      Tipo: {todolist_pb2.DeviceType.Name(announcement.type)}"
              f"  |  Atuador: {announcement.is_actuator}"
              f"  |  Endereco: {announcement.ip_address}:{announcement.port}\n")

    def _armazenar_leitura(self, sensor_data):
        """Adiciona a leitura ao self.sensor_history e atualiza last_seen (thread-safe)."""
        # 3. ATUALIZAR O HISTÓRICO DE SENSORES
        with self.lock:
            self.sensor_history.append({
                'device_id': sensor_data.device_id,
                'value':     sensor_data.value,
                'unit':      sensor_data.unit,
                'timestamp': sensor_data.timestamp   # Unix Epoch vindo do sensor
            })
            # Mantém last_seen sincronizado para detecção futura de falhas
            if sensor_data.device_id in self.active_devices:
                self.active_devices[sensor_data.device_id]['last_seen'] = time.time()

        print(f"[UDP] [sensor] Leitura de '{sensor_data.device_id}': "
              f"{sensor_data.value:.2f} {sensor_data.unit}")

    # -----------------------------------------------------------------------
    # 4. SERVIDOR TCP — escuta o Cliente Analítico
    # -----------------------------------------------------------------------

    def _thread_servidor_tcp(self):
        """Servidor TCP que aceita conexões do Cliente Analítico na GATEWAY_TCP_PORT.

        Para cada cliente aceito, delega o atendimento a _handle_client()
        em uma thread separada — o loop de accept() nunca fica bloqueado.
        """
        tcp_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR evita "Address already in use" ao reiniciar o Gateway
        tcp_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_server_socket.bind(('', GATEWAY_TCP_PORT))
        tcp_server_socket.listen(5)  # fila de até 5 conexões pendentes
        print(f"[TCP] Servidor escutando na porta {GATEWAY_TCP_PORT} (Cliente Analitico)")

        while True:
            conn, addr = tcp_server_socket.accept()
            print(f"[TCP] Cliente conectado: {addr}")
            # Thread daemon: morre automaticamente se o Gateway encerrar
            threading.Thread(
                target=self._handle_client,
                args=(conn, addr),
                daemon=True
            ).start()

    def _handle_client(self, conn, addr):
        """Processa uma única conexão de cliente TCP.

        Fluxo:
          1. Recebe os bytes enviados pelo cliente
          2. Desserializa em SmartCityMessage (Protobuf)
          3. Verifica se é um ClientRequest e despacha pelo campo 'command'
          4. Serializa e envia a GatewayResponse de volta
          5. Fecha a conexão
        """
        try:
            data = conn.recv(4096)
            if not data:
                return

            msg = todolist_pb2.SmartCityMessage()
            msg.ParseFromString(data)

            if not msg.HasField("client_request"):
                print(f"[TCP] Pacote de {addr} ignorado (nao e ClientRequest).")
                return

            request = msg.client_request
            print(f"[TCP] Comando recebido de {addr}: '{request.command}'")

            # --- Despachante de comandos ---
            response_msg = todolist_pb2.SmartCityMessage()

            if request.command == "PING":
                response_msg.gateway_response.status  = "SUCCESS"
                response_msg.gateway_response.message = "PONG"

            else:
                # Comando não reconhecido — resposta padrao de erro
                response_msg.gateway_response.status  = "ERROR"
                response_msg.gateway_response.message = f"Comando desconhecido: '{request.command}'"

            conn.sendall(response_msg.SerializeToString())
            print(f"[TCP] Resposta enviada para {addr}: "
                  f"status={response_msg.gateway_response.status}")

        except Exception as e:
            print(f"[TCP] Erro ao atender {addr}: {e}")
        finally:
            conn.close()  # garante fechamento mesmo em caso de excecao

    # -----------------------------------------------------------------------
    # 5. MÉTODO DE INICIALIZAÇÃO (start)
    # -----------------------------------------------------------------------

    def start(self):
        """Inicia todas as threads daemon e mantém o processo principal vivo."""
        print("=" * 52)
        print("   Gateway Inteligente — Cidade Inteligente")
        print("=" * 52)

        # Broadcaster Multicast: envia descoberta a cada DISCOVERY_INTERVAL s
        threading.Thread(
            target=self._thread_multicast_discovery,
            daemon=True,
            name="Discovery-Broadcaster"
        ).start()

        # Receptor UDP unificado: anúncios + dados de sensores na porta 5008
        threading.Thread(
            target=self._thread_ouvir_udp,
            daemon=True,
            name="UDP-Receiver"
        ).start()

        # Servidor TCP: aceita conexoes do Cliente Analitico na porta 5009
        threading.Thread(
            target=self._thread_servidor_tcp,
            daemon=True,
            name="TCP-Server"
        ).start()

        print("[Gateway] Em execução. Pressione Ctrl+C para sair.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[Gateway] Desligando.")


# ===========================================================================
if __name__ == "__main__":
    gateway = SmartCityGateway()
    gateway.start()

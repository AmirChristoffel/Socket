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
            existing = self.active_devices.get(announcement.device_id, {})
            self.active_devices[announcement.device_id] = {
                'type':        todolist_pb2.DeviceType.Name(announcement.type),
                'ip':          announcement.ip_address,
                'port':        announcement.port,
                'is_actuator': announcement.is_actuator,
                'last_seen':   time.time(),
                'state':       existing.get('state', False),
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
        """Recebe um ClientRequest Protobuf, despacha para o handler correto
        e devolve um GatewayResponse serializado pelo mesmo socket TCP."""
        try:
            data = conn.recv(4096)
            if not data:
                return

            msg = todolist_pb2.SmartCityMessage()
            msg.ParseFromString(data)

            if not msg.HasField("client_request"):
                print(f"[TCP] Pacote de {addr} ignorado (nao e ClientRequest).")
                return

            req = msg.client_request
            print(f"[TCP] Comando recebido de {addr}: '{req.command}'")

            # --- Despachante: cada comando chama seu auxiliar privado ---
            response_msg = todolist_pb2.SmartCityMessage()

            if req.command == "PING":
                response_msg.gateway_response.status  = "SUCCESS"
                response_msg.gateway_response.message = "PONG"

            elif req.command == "LIST_DEVICES":
                status, message = self._cmd_list_devices()
                response_msg.gateway_response.status  = status
                response_msg.gateway_response.message = message

            elif req.command == "GET_AVG":
                status, message = self._cmd_get_avg(req.target_device_id)
                response_msg.gateway_response.status  = status
                response_msg.gateway_response.message = message

            elif req.command == "GET_HISTORY":
                status, message = self._cmd_get_history()
                response_msg.gateway_response.status  = status
                response_msg.gateway_response.message = message

            elif req.command == "SET_STATE":
                status, message = self._cmd_set_state(
                    req.target_device_id, req.new_state
                )
                response_msg.gateway_response.status  = status
                response_msg.gateway_response.message = message

            else:
                response_msg.gateway_response.status  = "ERROR"
                response_msg.gateway_response.message = f"Comando desconhecido: '{req.command}'"

            conn.sendall(response_msg.SerializeToString())
            print(f"[TCP] Resposta para {addr}: status={response_msg.gateway_response.status}")

        except Exception as e:
            print(f"[TCP] Erro ao atender {addr}: {e}")
        finally:
            conn.close()

    # --- Auxiliares do despachante (uma responsabilidade cada) --------------

    def _cmd_list_devices(self):
        """Retorna uma string formatada com todos os dispositivos em active_devices."""
        with self.lock:
            # Copia o estado para liberar o lock antes de formatar
            snapshot = dict(self.active_devices)

        if not snapshot:
            return "ERROR", "Nenhum dispositivo conectado."

        lines = [f"  [{i+1}] {did} | tipo={info['type']} | "
                 f"atuador={info['is_actuator']} | ip={info['ip']}:{info['port']} | estado={info.get('state', False)}"
                 for i, (did, info) in enumerate(snapshot.items())]
        return "SUCCESS", f"{len(snapshot)} dispositivo(s):\n" + "\n".join(lines)

    def _cmd_get_avg(self, device_id):
        """Calcula a média das leituras de um sensor a partir do sensor_history."""
        if not device_id:
            return "ERROR", "target_device_id nao informado."

        with self.lock:
            readings = [r for r in self.sensor_history
                        if r['device_id'] == device_id]

        if not readings:
            return "ERROR", f"Sem leituras para '{device_id}'."

        avg  = sum(r['value'] for r in readings) / len(readings)
        unit = readings[-1]['unit']
        return "SUCCESS", f"Media de '{device_id}': {avg:.2f} {unit} ({len(readings)} leituras)"

    def _cmd_get_history(self, limit: int = 20):
        """Retorna as últimas `limit` leituras de todos os sensores."""
        with self.lock:
            recent = list(self.sensor_history[-limit:])
        if not recent:
            return "ERROR", "Sem leituras ainda."
        lines = [
            f"device_id={r['device_id']} | value={r['value']:.2f} | unit={r['unit']} | ts={r['timestamp']}"
            for r in recent
        ]
        return "SUCCESS", "\n".join(lines)

    def _cmd_set_state(self, device_id, new_state):
        """Repassa um ActuatorCommand via TCP diretamente ao atuador."""
        if not device_id:
            return "ERROR", "target_device_id nao informado."

        with self.lock:
            device = self.active_devices.get(device_id)

        if device is None:
            return "ERROR", f"Dispositivo '{device_id}' nao encontrado."

        if not device['is_actuator']:
            return "ERROR", f"'{device_id}' nao e um atuador."

        # Monta o comando Protobuf para o atuador
        cmd_msg = todolist_pb2.SmartCityMessage()
        cmd_msg.command.device_id = device_id
        cmd_msg.command.state     = new_state

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((device['ip'], device['port']))
                s.sendall(cmd_msg.SerializeToString())
            with self.lock:
                if device_id in self.active_devices:
                    self.active_devices[device_id]['state'] = new_state
            estado = "LIGADO" if new_state else "DESLIGADO"
            return "SUCCESS", f"'{device_id}' definido como {estado}."
        except (ConnectionRefusedError, TimeoutError, OSError):
            # Deteccao lazy de falha em atuadores: remove ao primeiro erro de conexao
            with self.lock:
                self.active_devices.pop(device_id, None)
            print(f"\n[ALERTA] Atuador '{device_id}' nao respondeu e foi removido!\n")
            return "ERROR", f"Atuador '{device_id}' estava offline e foi removido."

    # -----------------------------------------------------------------------
    # 5. MONITOR DE FALHAS (Heartbeat Checker)
    # -----------------------------------------------------------------------

    def _thread_monitor_falhas(self):
        """Verifica a cada 10 s se algum sensor parou de enviar dados.

        Lógica:
          - Sensores enviam leituras a cada 15 s.
          - Se last_seen > 35 s atrás (15 s de intervalo + 20 s de margem),
            considera o sensor morto e o remove de active_devices.
          - Atuadores não são verificados aqui: a detecção deles ocorre
            no momento do repasse em _cmd_set_state (falha lazy).
        """
        TIMEOUT_SENSOR = 35  # segundos sem dados → sensor considerado offline

        print("[Monitor] Thread de deteccao de falhas iniciada (intervalo=10s).")
        while True:
            time.sleep(10)
            agora = time.time()
            removidos = []

            with self.lock:
                for device_id in list(self.active_devices.keys()):
                    info = self.active_devices[device_id]
                    tempo_inativo = agora - info['last_seen']

                    if not info['is_actuator'] and tempo_inativo > TIMEOUT_SENSOR:
                        del self.active_devices[device_id]
                        removidos.append(device_id)

            # Imprime alertas fora do lock para não segurá-lo durante I/O
            for device_id in removidos:
                print(f"\n[ALERTA] Sensor '{device_id}' parou de enviar dados "
                      f"e foi removido da lista de ativos!\n")

    # -----------------------------------------------------------------------
    # 6. MÉTODO DE INICIALIZAÇÃO (start)
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

        # Monitor de falhas: remove dispositivos inativos periodicamente
        threading.Thread(
            target=self._thread_monitor_falhas,
            daemon=True,
            name="Fault-Monitor"
        ).start()

        print("[Gateway] Em execucao. Pressione Ctrl+C para sair.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[Gateway] Desligando.")


# ===========================================================================
if __name__ == "__main__":
    gateway = SmartCityGateway()
    gateway.start()

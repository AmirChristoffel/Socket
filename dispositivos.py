# dispositivos.py (Versão Refatorada com Lógica de Rede)

import socket
import struct
import threading
import time
import uuid

# Importa as classes de mensagem do arquivo compilado
from protos import todolist_pb2

# --- Configurações de Rede (Comuns a todos) ---
MULTICAST_GROUP = '224.1.1.1'
MULTICAST_PORT = 5007

class Dispositivos:
    """Classe base para todos os dispositivos da cidade inteligente."""
    
    def __init__(self, tipo):
        self.device_id = f"{tipo.lower().replace(' ', '_')}_{str(uuid.uuid4())[:4]}"
        self.tipo = tipo
        self.ip = '127.0.0.1'  # IP do próprio dispositivo para o servidor TCP
        self.port = 0          # Porta TCP será alocada dinamicamente
        self.estado = False    # Estado padrão (ex: desligado)
        self.is_actuator = False # Por padrão, um dispositivo é um sensor
        # [NOVO] Endereço do Gateway descoberto dinamicamente via Multicast.
        # Preenchido por listen_for_discovery ao receber a mensagem GatewayDiscovery.
        self.gateway_address = None

    def __str__(self):
        return f"ID: {self.device_id}, Tipo: {self.tipo}, Endereço: {self.ip}:{self.port}, Estado: {'Ligado' if self.estado else 'Desligado'}"

    def iniciar(self):
        """Inicia os processos de descoberta e o servidor de comandos em threads."""
        print(f"Iniciando dispositivo: {self.device_id}")
        
        # Inicia o servidor TCP em uma thread para escutar por comandos
        tcp_thread = threading.Thread(target=self.start_tcp_server, daemon=True)
        tcp_thread.start()
        
        # Aguarda um instante para garantir que a porta TCP foi alocada
        time.sleep(1)

        # Inicia o listener de descoberta multicast em outra thread
        discovery_thread = threading.Thread(target=self.listen_for_discovery, daemon=True)
        discovery_thread.start()

        # Mantém o programa principal rodando
        print(f"{self.device_id} iniciado. Pressione Ctrl+C para sair.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\nDesligando {self.device_id}.")

    def start_tcp_server(self):
        """Lógica do servidor TCP que fica aguardando conexões do Gateway."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind((self.ip, 0))
        self.port = server.getsockname()[1] # Captura a porta alocada
        server.listen(5)
        print(f"[{self.device_id}] Servidor TCP escutando em {self.ip}:{self.port}")

        while True:
            conn, addr = server.accept()
            print(f"[{self.device_id}] Gateway conectado via TCP em {addr}")
            # Delega o tratamento da conexão para um método que pode ser sobrescrito
            self.handle_connection(conn)

    def listen_for_discovery(self):
        """Lógica do listener UDP Multicast para ser descoberto pelo Gateway."""
        multicast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        multicast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        multicast_socket.bind(('', MULTICAST_PORT))

        mreq = struct.pack("4sl", socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
        multicast_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        print(f"[{self.device_id}] Aguardando descoberta em {MULTICAST_GROUP}:{MULTICAST_PORT}")

        while True:
            data, address = multicast_socket.recvfrom(1024)

            # [MUDANÇA] Desserializa a mensagem Protobuf recebida.
            # Antes: o gateway enviava bytes puros b'GATEWAY_DISCOVERY', sem parse.
            # Agora: o gateway envia SmartCityMessage com gateway_discovery.data_port.
            try:
                msg = todolist_pb2.SmartCityMessage()
                msg.ParseFromString(data)

                # [CORREÇÃO] Campo renomeado de gateway_discovery → discovery no .proto
                if msg.HasField("discovery"):
                    # Extrai IP do remetente e porta de dados do Protobuf
                    gateway_ip = address[0]
                    gateway_data_port = msg.discovery.data_port
                    self.gateway_address = (gateway_ip, gateway_data_port)
                    print(f"\n[{self.device_id}] Gateway descoberto em {self.gateway_address}")
                    # Envia anúncio para a PORTA DE DADOS (5008), não para a porta
                    # efêmera do broadcaster — o receptor unificado escuta lá
                    self.send_announcement(self.gateway_address)
                else:
                    print(f"[{self.device_id}] Mensagem Multicast ignorada (não é discovery).")

            except Exception as e:
                print(f"[{self.device_id}] Erro ao processar mensagem Multicast de {address}: {e}")

    def send_announcement(self, gateway_address):
        """Envia a mensagem de anúncio (Protocol Buffers) para o Gateway."""
        response_message = todolist_pb2.SmartCityMessage()
        
        proto_device_type = getattr(todolist_pb2, self.tipo.upper(), todolist_pb2.UNKNOWN)

        response_message.announcement.device_id = self.device_id
        response_message.announcement.type = proto_device_type
        response_message.announcement.ip_address = self.ip
        response_message.announcement.port = self.port
        response_message.announcement.is_actuator = self.is_actuator

        response_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        response_socket.sendto(response_message.SerializeToString(), gateway_address)
        response_socket.close()
        print(f"[{self.device_id}] Anúncio enviado para o Gateway.")
        
    def handle_connection(self, conn):
        """Método placeholder para lidar com conexões TCP. Será sobrescrito."""
        print(f"[{self.device_id}] Conexão recebida, mas nenhum handler definido.")
        conn.close()


class Atuador(Dispositivos):
    """Classe para dispositivos que recebem comandos, como postes e semáforos."""
    
    def __init__(self, tipo):
        super().__init__(tipo=tipo)
        self.is_actuator = True # Define que este tipo de dispositivo é um atuador

    def handle_connection(self, conn):
        """Sobrescreve o método da classe pai para tratar comandos específicos de atuadores."""
        try:
            data = conn.recv(1024)
            if data:
                command_msg = todolist_pb2.SmartCityMessage()
                command_msg.ParseFromString(data)

                if command_msg.HasField("command"):
                    command = command_msg.command
                    self.estado = command.state # Atualiza o estado
                    print(f"[{self.device_id}] Comando recebido: {'Ligar' if self.estado else 'Desligar'}")
                    print(f"[{self.device_id}] Novo estado: {'Ligado' if self.estado else 'Desligado'}")
        except Exception as e:
            print(f"[{self.device_id}] Erro ao processar comando: {e}")
        finally:
            conn.close()

# Adicione esta classe no final do arquivo dispositivos.py

class Continuos(Dispositivos):
    """Classe para dispositivos que enviam dados continuamente, como sensores."""
    
    def __init__(self, tipo, data_unit=""):
        super().__init__(tipo=tipo)
        self.is_actuator = False # Sensores não são atuadores
        self.data_unit = data_unit # Ex: "Celsius", "µg/m³"

    def iniciar(self):
        """Sobrescreve o método iniciar para sensores."""
        print(f"Iniciando sensor: {self.device_id}")

        # Inicia a thread de descoberta — ela vai preencher self.gateway_address
        discovery_thread = threading.Thread(target=self.listen_for_discovery, daemon=True)
        discovery_thread.start()

        # [MUDANÇA] Não passa endereço hardcoded como argumento.
        # start_sending_data aguarda self.gateway_address ser preenchido pela descoberta.
        data_thread = threading.Thread(target=self.start_sending_data, daemon=True)
        data_thread.start()

        print(f"{self.device_id} iniciado. Pressione Ctrl+C para sair.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\nDesligando {self.device_id}.")

    def start_sending_data(self):
        """Envia dados simulados para o gateway a cada 15 segundos via UDP.

        [MUDANÇA] Aguarda self.gateway_address ser preenchido dinamicamente
        por listen_for_discovery antes de começar a enviar qualquer dado.
        Antes: recebia gateway_address como argumento fixo ('127.0.0.1', 5008).
        """
        import random

        # Bloqueia até o Gateway ser descoberto via Multicast
        print(f"[{self.device_id}] Aguardando descoberta do Gateway para iniciar envio...")
        while self.gateway_address is None:
            time.sleep(2)

        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print(f"[{self.device_id}] Gateway encontrado. Enviando dados para {self.gateway_address}")

        while True:
            # Simula uma leitura de sensor
            if self.tipo == "TEMPERATURE_SENSOR":
                leitura = round(random.uniform(18.0, 35.0), 2)
            else:
                leitura = round(random.uniform(0.0, 100.0), 2)

            print(f"[{self.device_id}] Nova leitura: {leitura} {self.data_unit}")

            # Monta a mensagem Protobuf
            # [NOVO] timestamp=int(time.time()) registra o momento exato da leitura
            sensor_payload = todolist_pb2.SensorData(
                device_id=self.device_id,
                value=leitura,
                unit=self.data_unit,
                timestamp=int(time.time())
            )
            response_message = todolist_pb2.SmartCityMessage(sensor_data=sensor_payload)

            # [MUDANÇA] Usa self.gateway_address (dinâmico) em vez de endereço fixo
            udp_socket.sendto(response_message.SerializeToString(), self.gateway_address)

            time.sleep(15)
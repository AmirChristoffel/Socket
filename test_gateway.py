# test_gateway.py (VERSÃO ATUALIZADA para ouvir sensores)

import socket
import time
import threading

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))
from protos import todolist_pb2

# --- Configurações de Rede ---
MULTICAST_GROUP = '224.1.1.1'
MULTICAST_PORT = 5007
GATEWAY_DATA_PORT = 5008 # Porta onde o gateway vai ouvir dados dos sensores

def listen_for_sensor_data():
    """Cria um servidor UDP para receber dados contínuos dos sensores."""
    udp_server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_server_socket.bind(('', GATEWAY_DATA_PORT))
    print(f"[Gateway-Sensor-Listener] Ouvindo por dados de sensores na porta UDP {GATEWAY_DATA_PORT}")

    while True:
        data, addr = udp_server_socket.recvfrom(1024)
        
        sensor_msg = todolist_pb2.SmartCityMessage()
        sensor_msg.ParseFromString(data)

        if sensor_msg.HasField("sensor_data"):
            sensor_data = sensor_msg.sensor_data
            print(f"\n[DADOS RECEBIDOS] Do Sensor '{sensor_data.device_id}': {sensor_data.value:.2f} {sensor_data.unit}")

def discover_and_command_device():
    """Envia uma mensagem de descoberta e lida com a resposta."""
    
    multicast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    multicast_socket.settimeout(5.0) # Aumentar um pouco o timeout
    
    discovery_message = b'GATEWAY_DISCOVERY'
    
    try:
        print("Enviando mensagem de descoberta para o grupo multicast...")
        multicast_socket.sendto(discovery_message, (MULTICAST_GROUP, MULTICAST_PORT))

        print("Aguardando resposta dos dispositivos...")
        data, addr = multicast_socket.recvfrom(1024)
        
        announcement = todolist_pb2.SmartCityMessage()
        announcement.ParseFromString(data)

        if not announcement.HasField("announcement"):
            print("Resposta recebida não é um anúncio de dispositivo.")
            return

        device_info = announcement.announcement
        device_type_name = todolist_pb2.DeviceType.Name(device_info.type)
        print(f"\nDispositivo encontrado!")
        print(f"  ID: {device_info.device_id}")
        print(f"  Tipo: {device_type_name}")
        print(f"  Endereço: {device_info.ip_address}:{device_info.port}")

        if device_info.is_actuator:
            print("\nO dispositivo é um atuador. Enviando comando para LIGAR...")
            time.sleep(1)

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_socket:
                tcp_socket.connect((device_info.ip_address, device_info.port))
                
                command_msg = todolist_pb2.SmartCityMessage()
                command_msg.command.state = True

                tcp_socket.send(command_msg.SerializeToString())
                print("Comando enviado com sucesso.")
        else:
            print("\nO dispositivo é um sensor. O Gateway irá aguardar os dados na sua porta de escuta.")

    except socket.timeout:
        print("Nenhum dispositivo novo respondeu a tempo.")
    finally:
        multicast_socket.close()

if __name__ == "__main__":
    # Inicia o listener de dados de sensores em uma thread separada
    sensor_listener_thread = threading.Thread(target=listen_for_sensor_data, daemon=True)
    sensor_listener_thread.start()
    
    # Executa a descoberta de dispositivos
    discover_and_command_device()

    # Mantém o programa principal rodando para continuar recebendo dados dos sensores
    print("\nGateway em modo de escuta. Pressione Ctrl+C para sair.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nGateway desligado.")
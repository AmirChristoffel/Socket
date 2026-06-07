use std::net::{UdpSocket, Ipv4Addr, SocketAddr};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use uuid::Uuid;

use protobuf::Message;

pub mod proto {
    include!(concat!(env!("OUT_DIR"), "/protos/mod.rs"));
}

use proto::todolist::{SmartCityMessage, DeviceAnnouncement, SensorData, DeviceType};
use proto::todolist::smart_city_message::Payload;

fn main() -> std::io::Result<()> {
    // 1. Gerar ID unico para este sensor em Rust
    let device_id = format!("rust_temp_sensor_{}", &Uuid::new_v4().to_string()[..4]);
    println!("=== Dispositivo IoT em Rust Iniciado ===");
    println!("Device ID: {}", device_id);

    // 2. Configurar o socket UDP para ouvir Multicast na porta 5007
    let multicast_addr = "0.0.0.0:5007".parse::<SocketAddr>().unwrap();
    let socket_multicast = UdpSocket::bind(multicast_addr)?;

    // Entrar no grupo Multicast
    let multicast_group = Ipv4Addr::new(224, 1, 1, 1);
    let interface = Ipv4Addr::new(0, 0, 0, 0); // Ouvir em todas as interfaces locais
    socket_multicast.join_multicast_v4(&multicast_group, &interface)?;

    println!("[Multicast] Aguardando broadcast de descoberta do Gateway (porta 5007)...");

    let mut buf = [0u8; 4096];
    
    // 3. Loop principal
    loop {
        // Recebe o pacote multicast
        let (amt, src) = socket_multicast.recv_from(&mut buf)?;
        let data = &buf[..amt];

        // Tenta desserializar o envelope SmartCityMessage
        if let Ok(msg) = SmartCityMessage::parse_from_bytes(data) {
            if let Some(Payload::Discovery(disc)) = msg.payload {
                let gateway_ip = src.ip();
                let gateway_data_port = disc.data_port as u16;
                let gateway_dest = SocketAddr::new(gateway_ip, gateway_data_port);
                
                println!("\n[Discovery] Gateway detectado em {:?}", src);
                println!("[Discovery] Porta de dados do Gateway: {}", gateway_data_port);

                // Criar socket unicast UDP para comunicacao de dados
                let unicast_socket = UdpSocket::bind("0.0.0.0:0")?;
                
                // 4. Enviar DeviceAnnouncement
                let mut announce = DeviceAnnouncement::new();
                announce.device_id = device_id.clone();
                announce.type_ = protobuf::EnumOrUnknown::new(DeviceType::TEMPERATURE_SENSOR);
                announce.ip_address = "127.0.0.1".to_string();
                announce.port = 0; // Sensores continuos nao ouvem comandos TCP
                announce.is_actuator = false;

                let mut announce_msg = SmartCityMessage::new();
                announce_msg.payload = Some(Payload::Announcement(announce));

                let out_buf = announce_msg.write_to_bytes().unwrap();
                unicast_socket.send_to(&out_buf, gateway_dest)?;
                println!("[Registro] Anuncio de dispositivo enviado para o Gateway.");

                // 5. Envio de leituras periodicas (simulado)
                println!("[Telemetria] Iniciando envio de leituras a cada 15 segundos...");
                loop {
                    // Simular temperatura de 20.0 a 30.0 graus Celsius
                    let seconds = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap()
                        .as_secs();
                    
                    // Um valor oscilante simples
                    let simulated_value = 20.0 + ((seconds % 100) as f32 / 10.0);

                    let mut sensor_data = SensorData::new();
                    sensor_data.device_id = device_id.clone();
                    sensor_data.value = simulated_value;
                    sensor_data.unit = "Celsius".to_string();
                    sensor_data.timestamp = seconds as i64;

                    let mut data_msg = SmartCityMessage::new();
                    data_msg.payload = Some(Payload::SensorData(sensor_data));

                    let telemetry_buf = data_msg.write_to_bytes().unwrap();
                    
                    if let Err(e) = unicast_socket.send_to(&telemetry_buf, gateway_dest) {
                        println!("[Erro] Falha ao enviar telemetria: {}. Retornando ao discovery...", e);
                        break;
                    }
                    
                    println!("[Telemetria] Enviado: {} = {:.1} Celsius (timestamp: {})", device_id, simulated_value, seconds);
                    
                    thread::sleep(Duration::from_secs(15));
                }
            }
        }
    }
}

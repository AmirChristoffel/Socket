use std::env;
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

/// Envia o anuncio e inicia o loop de telemetria para um gateway conhecido.
fn run_sensor(device_id: &str, gateway_dest: SocketAddr) -> std::io::Result<()> {
    let unicast_socket = UdpSocket::bind("0.0.0.0:0")?;

    // Enviar DeviceAnnouncement
    let mut announce = DeviceAnnouncement::new();
    announce.device_id = device_id.to_string();
    announce.type_ = protobuf::EnumOrUnknown::new(DeviceType::TEMPERATURE_SENSOR);
    announce.ip_address = "127.0.0.1".to_string();
    announce.port = 0;
    announce.is_actuator = false;

    let mut announce_msg = SmartCityMessage::new();
    announce_msg.payload = Some(Payload::Announcement(announce));

    let out_buf = announce_msg.write_to_bytes().unwrap();
    unicast_socket.send_to(&out_buf, gateway_dest)?;
    println!("[Registro] Anuncio enviado para o Gateway em {}.", gateway_dest);

    // Loop de telemetria
    println!("[Telemetria] Enviando leituras a cada 15 segundos...");
    loop {
        let seconds = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let simulated_value = 20.0 + ((seconds % 100) as f32 / 10.0);

        let mut sensor_data = SensorData::new();
        sensor_data.device_id = device_id.to_string();
        sensor_data.value = simulated_value;
        sensor_data.unit = "Celsius".to_string();
        sensor_data.timestamp = seconds as i64;

        let mut data_msg = SmartCityMessage::new();
        data_msg.payload = Some(Payload::SensorData(sensor_data));

        let telemetry_buf = data_msg.write_to_bytes().unwrap();

        if let Err(e) = unicast_socket.send_to(&telemetry_buf, gateway_dest) {
            println!("[Erro] Falha ao enviar telemetria: {}. Encerrando.", e);
            break;
        }

        println!("[Telemetria] {} = {:.1} Celsius (ts: {})", device_id, simulated_value, seconds);
        thread::sleep(Duration::from_secs(15));
    }
    Ok(())
}

fn main() -> std::io::Result<()> {
    let device_id = format!("rust_temp_sensor_{}", &Uuid::new_v4().to_string()[..4]);
    println!("=== Dispositivo IoT em Rust Iniciado ===");
    println!("Device ID: {}", device_id);

    // Se GATEWAY_ADDR=ip:porta estiver definido (iniciado via API), pula o multicast
    // e conecta direto — evita o erro de bind na porta 5007 no Windows.
    if let Ok(addr_str) = env::var("GATEWAY_ADDR") {
        let gateway_dest: SocketAddr = addr_str
            .parse()
            .expect("GATEWAY_ADDR invalido (formato esperado: ip:porta)");
        println!("[Config] Gateway via GATEWAY_ADDR: {}", gateway_dest);
        return run_sensor(&device_id, gateway_dest);
    }

    // Modo normal: descoberta via Multicast
    println!("[Multicast] Aguardando broadcast de descoberta do Gateway (porta 5007)...");

    let multicast_addr = "0.0.0.0:5007".parse::<SocketAddr>().unwrap();
    let socket_multicast = UdpSocket::bind(multicast_addr)?;

    let multicast_group = Ipv4Addr::new(224, 1, 1, 1);
    let interface = Ipv4Addr::new(0, 0, 0, 0);
    socket_multicast.join_multicast_v4(&multicast_group, &interface)?;

    let mut buf = [0u8; 4096];

    loop {
        let (amt, src) = socket_multicast.recv_from(&mut buf)?;
        let data = &buf[..amt];

        if let Ok(msg) = SmartCityMessage::parse_from_bytes(data) {
            if let Some(Payload::Discovery(disc)) = msg.payload {
                let gateway_dest = SocketAddr::new(src.ip(), disc.data_port as u16);
                println!("\n[Discovery] Gateway detectado em {:?}", src);
                run_sensor(&device_id, gateway_dest)?;
                // Se run_sensor retornar (erro de rede), volta ao loop de discovery
            }
        }
    }
}

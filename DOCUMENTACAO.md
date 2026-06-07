# Documentação Técnica — Smart City IoT: Gateway Inteligente (SD)

> **Disciplina:** Sistemas Distribuídos
> **Objetivo:** Referência técnica completa da arquitetura, protocolos, componentes e decisões de design.

---

## Índice

1. [Visão Geral](#1-visão-geral)
2. [Estrutura de Arquivos](#2-estrutura-de-arquivos)
3. [Arquitetura de Rede e Protocolos](#3-arquitetura-de-rede-e-protocolos)
4. [Componentes — Código e Responsabilidades](#4-componentes--código-e-responsabilidades)
   - 4.1 [gateway.py](#41-gatewaypy)
   - 4.2 [dispositivos.py](#42-dispositivospy)
   - 4.3 [cliente.py](#43-clientepy)
   - 4.4 [api.py — Bridge REST/SSE](#44-apipy--bridge-restsse)
   - 4.5 [dispositivo_rust/src/main.rs](#45-dispositivo_rustsrcmainrs)
   - 4.6 [Dashboard (Next.js)](#46-dashboard-nextjs)
5. [Protocol Buffers — Schema e Mensagens](#5-protocol-buffers--schema-e-mensagens)
6. [Comandos do Gateway (TCP)](#6-comandos-do-gateway-tcp)
7. [API REST — Referência de Endpoints](#7-api-rest--referência-de-endpoints)
8. [SSE — Stream de Eventos em Tempo Real](#8-sse--stream-de-eventos-em-tempo-real)
9. [Decisões de Design](#9-decisões-de-design)
10. [Detecção de Falhas e Tolerância](#10-detecção-de-falhas-e-tolerância)
11. [Configuração e Variáveis de Ambiente](#11-configuração-e-variáveis-de-ambiente)
12. [Guia de Execução](#12-guia-de-execução)

---

## 1. Visão Geral

O sistema implementa uma rede IoT para cidade inteligente com três camadas:

```
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA DE VISUALIZAÇÃO                                         │
│  Dashboard Next.js :3000  ←──SSE──  api.py FastAPI :8000        │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP / TCP 5009
┌──────────────────────────────▼──────────────────────────────────┐
│  CAMADA DE COORDENAÇÃO                                          │
│                  Gateway (gateway.py)                           │
│  UDP Multicast 5007 ◄──► UDP Unicast 5008 ◄──► TCP 5009        │
└───────────┬───────────────────────────────────────┬─────────────┘
            │ UDP Multicast                          │ TCP dinâmico
┌───────────▼───────────────────────────────────────▼─────────────┐
│  CAMADA DE DISPOSITIVOS                                         │
│  Sensores (UDP apenas)        Atuadores (TCP bidirecional)      │
│  • Continuos (Python)         • Atuador (Python)                │
│  • Rust sensor                • SensorControlavel (Python)      │
└─────────────────────────────────────────────────────────────────┘
```

**Fluxo de dados resumido:**
1. Gateway envia broadcasts UDP Multicast (:5007) a cada 5 s
2. Dispositivos respondem com anúncio UDP para :5008
3. Sensores enviam leituras UDP para :5008 a cada 15 s
4. Cliente/API consulta e comanda via TCP :5009
5. Gateway abre TCP dinâmico para comandar atuadores

---

## 2. Estrutura de Arquivos

```
SD_Socktes/
├── .gitignore
├── README.md               Guia rápido de execução
├── DOCUMENTACAO.md         Esta documentação
│
├── gateway.py              Gateway: discovery, UDP recv, TCP server, fault monitor
├── dispositivos.py         Classes base dos dispositivos Python
├── cliente.py              Cliente analítico CLI
├── api.py                  Bridge FastAPI: subprocessos + proxy Gateway + SSE
│
├── protos/
│   ├── todolist.proto      Schema Protobuf (define todas as mensagens)
│   └── todolist_pb2.py     Código Python gerado pelo protoc
│
├── dispositivo_rust/
│   ├── Cargo.toml          Dependências Rust (prost, tokio, etc.)
│   ├── build.rs            Compila o .proto durante cargo build
│   └── src/main.rs         Sensor de temperatura em Rust
│
└── dashboard/
    ├── app/page.tsx        UI principal (componentes React)
    ├── hooks/useSSE.ts     Hook para consumir o stream SSE da API
    └── lib/api.ts          Funções de acesso à API REST + tipos TypeScript
```

---

## 3. Arquitetura de Rede e Protocolos

### Mapa de portas

| Porta | Protocolo | Direção | Propósito |
|---|---|---|---|
| **5007** | UDP Multicast | Gateway → Todos | Broadcast de descoberta (`GatewayDiscovery`) |
| **5008** | UDP Unicast | Dispositivos → Gateway | Anúncios (`DeviceAnnouncement`) + leituras (`SensorData`) |
| **5009** | TCP | Cliente → Gateway | Comandos analíticos (`ClientRequest` / `GatewayResponse`) |
| **dinâmica** | TCP | Gateway → Atuador | Comandos de controle (`ActuatorCommand`) |
| **8000** | HTTP/SSE | Dashboard → API | REST + Server-Sent Events |
| **3000** | HTTP | Navegador → Dashboard | Interface Next.js |

### Por que cada protocolo

**UDP Multicast (:5007) — Descoberta**
- Uma única transmissão alcança todos os dispositivos presentes na rede local
- Nenhum endereço precisa ser configurado antecipadamente
- TTL=2 confina o tráfego à LAN
- Gateway envia a cada 5 s: dispositivos novos se registram sem intervenção manual

**UDP Unicast (:5008) — Telemetria**
- Dados de sensor são descartáveis: se um pacote se perde, o próximo chega em 15 s
- Sem overhead de conexão — o Gateway não mantém estado por sensor
- Uma porta unificada recebe tanto anúncios quanto leituras (campo `oneof` do Protobuf faz o roteamento)

**TCP (:5009) — Comandos do cliente**
- Entrega garantida por retransmissão automática (ACK)
- Ordem preservada — crítico para mensagens Protobuf binárias
- Falha de conexão é imediatamente visível (`ConnectionRefusedError`)

**TCP dinâmico — Controle de atuadores**
- Cada dispositivo faz `bind('127.0.0.1', 0)` — o SO aloca a porta disponível
- A porta é capturada com `getsockname()[1]` e enviada no `DeviceAnnouncement.port`
- Gateway usa essa porta para enviar `ActuatorCommand` diretamente ao dispositivo

---

## 4. Componentes — Código e Responsabilidades

### 4.1 gateway.py

**Classe:** `SmartCityGateway`

**Estruturas de estado (compartilhadas entre threads):**
```python
self.active_devices = {}    # device_id → {type, ip, port, is_actuator, last_seen, state}
self.sensor_history = []    # lista plana de {device_id, value, unit, timestamp}
self.lock = threading.Lock()
```

**Threads:**

| Thread | Nome | Responsabilidade |
|---|---|---|
| `_thread_multicast_discovery` | `Discovery-Broadcaster` | Envia `GatewayDiscovery` via UDP Multicast a cada 5 s |
| `_thread_ouvir_udp` | `UDP-Receiver` | Recebe anúncios e leituras na porta 5008; roteia para handlers |
| `_thread_servidor_tcp` | `TCP-Server` | Aceita conexões TCP na porta 5009; delega para `_handle_client` em nova thread |
| `_thread_monitor_falhas` | `Fault-Monitor` | A cada 10 s: remove sensores sem dados (25 s) e testa conectividade de atuadores (TCP 1 s) |

**Handlers UDP:**
- `_registrar_dispositivo(announcement)` — insere ou atualiza `active_devices`; preserva `state` existente em re-registro
- `_armazenar_leitura(sensor_data)` — adiciona à `sensor_history`; atualiza `last_seen` do sensor

**Despachante TCP (`_handle_client`):**
Recebe um `ClientRequest`, identifica o campo `command` e chama o auxiliar adequado:

```
PING        → "PONG"
LIST_DEVICES → _cmd_list_devices()
GET_AVG      → _cmd_get_avg(target_device_id)
GET_HISTORY  → _cmd_get_history(limit=20)
SET_STATE    → _cmd_set_state(target_device_id, new_state)
```

**Monitor de falhas (`_thread_monitor_falhas`):**
```python
TIMEOUT_SENSOR = 25  # segundos sem pacote UDP → sensor removido

for each device in snapshot:
    if sensor:   remove if time.time() - last_seen > 25
    if actuator: tenta TCP connect(timeout=1s); remove se OSError
```

---

### 4.2 dispositivos.py

**Hierarquia de classes:**

```
Dispositivos (base)
│  device_id (uuid4[:4]), tipo, ip, port=0, estado, is_actuator=False, gateway_address=None
│  iniciar() → start_tcp_server() + listen_for_discovery()
│  listen_for_discovery() → multicast join OU GATEWAY_ADDR env var
│  send_announcement() → DeviceAnnouncement UDP para o Gateway
│
├── Atuador (is_actuator=True)
│   handle_connection() → recebe ActuatorCommand, atualiza self.estado
│
└── Continuos (is_actuator=False)
    iniciar() → listen_for_discovery() + start_sending_data() (sem TCP server)
    start_sending_data() → SensorData UDP a cada 15 s
    │
    └── SensorControlavel (is_actuator=True)
        _servidor_tcp() → recebe ActuatorCommand (threshold + estado)
        iniciar() → _servidor_tcp() + super().iniciar()
        start_sending_data() → igual Continuos + alerta de threshold
```

**`listen_for_discovery` — fallback GATEWAY_ADDR:**
```python
gateway_env = os.environ.get("GATEWAY_ADDR")
if gateway_env:
    ip, port = gateway_env.split(":")
    self.gateway_address = (ip, int(port))
    self.send_announcement(self.gateway_address)
    return   # pula o bind multicast
# caso contrário: join ao grupo 224.1.1.1:5007 e aguarda
```
Isso resolve o `WinError 10013` (acesso negado ao bind multicast) quando múltiplos processos rodam no mesmo Windows.

---

### 4.3 cliente.py

Cliente analítico em modo texto. Opera em um loop de menu.

**Função central:**
```python
def enviar_comando(req: ClientRequest) -> Optional[str]:
    # Abre TCP para 127.0.0.1:5009
    # Serializa SmartCityMessage(client_request=req)
    # Recebe GatewayResponse e retorna .message
    # Em falha: retorna None (sem crash — exceções capturadas)
```

**Timeout:** `s.settimeout(5)` — nunca trava indefinidamente.

---

### 4.4 api.py — Bridge REST/SSE

**FastAPI** rodando na porta 8000. Três responsabilidades:

#### 4.4.1 Gestão de subprocessos

Define 7 **slots** fixos com nome, comando e diretório de trabalho:

| Slot | Label | Comando |
|---|---|---|
| `gateway` | Gateway Inteligente | `python gateway.py` |
| `sensor_temperatura_py` | Sensor de Temperatura (Python) | `Continuos('TEMPERATURE_SENSOR','Celsius').iniciar()` |
| `sensor_ar` | Sensor de Qualidade do Ar | `SensorControlavel('AIR_QUALITY_SENSOR','ug/m3').iniciar()` |
| `poste` | Poste de Luz | `Atuador('LAMP_POST').iniciar()` |
| `semaforo` | Semáforo | `Atuador('TRAFFIC_LIGHT').iniciar()` |
| `camera` | Câmera | `Atuador('CAMERA').iniciar()` |
| `sensor_rust` | Sensor de Temperatura (Rust) | `cargo run` em `dispositivo_rust/` |

Para cada slot não-`gateway`, `GATEWAY_ADDR=127.0.0.1:5008` é injetado no ambiente para evitar o bind multicast:
```python
if slot != "gateway":
    env["GATEWAY_ADDR"] = "127.0.0.1:5008"
```

`stdout` e `stderr` de cada processo são redirecionados para `logs/{slot}.log`.

#### 4.4.2 Proxy para o Gateway

Helper `_tcp(req)` abre TCP para `127.0.0.1:5009`, envia o `ClientRequest` e retorna a string da resposta (ou `None` em falha, timeout=3 s).

Dois parsers de string:
- `_parse_devices(raw)` — converte saída de `LIST_DEVICES` em lista de dicts
- `_parse_readings(raw)` — converte saída de `GET_HISTORY` (`device_id=x | value=y | unit=z | ts=w`) em lista de dicts

#### 4.4.3 SSE — Stream de eventos

`GET /events` retorna um `StreamingResponse` com media type `text/event-stream`. A cada 3 s emite:
- `event: processes` — status de todos os slots (running true/false)
- `event: gateway` — se o Gateway responde ao PING
- `event: devices` — lista de dispositivos (só se gateway online)
- `event: readings` — últimas 20 leituras do GET_HISTORY (só se gateway online)

---

### 4.5 dispositivo_rust/src/main.rs

Implementa o mesmo papel de um `Continuos` Python em Rust, demonstrando interoperabilidade por Protobuf.

**Fluxo:**
1. Verifica `GATEWAY_ADDR` env var — se presente, usa diretamente; caso contrário, faz join multicast e aguarda `GatewayDiscovery`
2. Envia `DeviceAnnouncement` (`TEMPERATURE_SENSOR`, `is_actuator=false`)
3. Loop de telemetria a cada 15 s: temperatura simulada (20–30 °C), envia `SensorData` via UDP

`build.rs` compila `protos/todolist.proto` automaticamente via `prost-build` durante o `cargo build`.

---

### 4.6 Dashboard (Next.js)

**`dashboard/lib/api.ts`** — tipos TypeScript e funções fetch:
- `ProcessStatus`, `Device`, `Reading` — contratos de dados
- `startProcess(slot)`, `stopProcess(slot)`, `sendCommand(deviceId, state)`, etc.

**`dashboard/hooks/useSSE.ts`** — hook `useSSE()`:
- Cria `EventSource("http://localhost:8000/events")`
- Registra listeners para `processes`, `gateway`, `devices`, `readings`
- Detecta dispositivos novos/removidos comparando com `knownIdsRef` e gera log
- Expõe: `processes`, `gatewayOnline`, `devices`, `readings`, `log`, `connected`, `connect()`, `disconnect()`

**`dashboard/app/page.tsx`** — componentes principais:

| Componente | Função |
|---|---|
| `DeviceRow` | Linha na tabela de dispositivos; mostra tipo, IP, estado (Ligado/Desligado/Ativo) |
| `ClientTerminal` | Painel com botões PING, LIST_DEVICES, Conectar/Desligar cliente |
| `LiveReadings` | Lista as últimas leituras de sensores em tempo real (ordem decrescente) |
| `ProcessCard` | Card de cada slot com botão Iniciar/Parar e badge de status |

---

## 5. Protocol Buffers — Schema e Mensagens

Arquivo: `protos/todolist.proto`

**Envelope único:**
```protobuf
message SmartCityMessage {
  oneof payload {
    GatewayDiscovery    discovery        = 1;
    DeviceAnnouncement  announcement     = 2;
    SensorData          sensor_data      = 3;
    ActuatorCommand     command          = 4;
    ClientRequest       client_request   = 5;
    GatewayResponse     gateway_response = 6;
  }
}
```

O receptor verifica qual campo está preenchido com `HasField()` e roteia sem precisar de portas separadas por tipo de mensagem.

**Mensagens:**

| Mensagem | Campos principais | Usado em |
|---|---|---|
| `GatewayDiscovery` | `data_port` (int) | UDP Multicast 5007 → dispositivos |
| `DeviceAnnouncement` | `device_id`, `type` (DeviceType enum), `ip_address`, `port`, `is_actuator` | UDP 5008 → Gateway |
| `SensorData` | `device_id`, `value` (float), `unit`, `timestamp` (int) | UDP 5008 → Gateway |
| `ActuatorCommand` | `device_id`, `state` (bool), `threshold` (float), `enabled` (bool) | TCP dinâmico → atuadores |
| `ClientRequest` | `command` (string), `target_device_id`, `new_state` (bool) | TCP 5009 → Gateway |
| `GatewayResponse` | `status` ("SUCCESS"/"ERROR"), `message` (string) | TCP 5009 → cliente |

**DeviceType enum:** `UNKNOWN`, `TEMPERATURE_SENSOR`, `AIR_QUALITY_SENSOR`, `LAMP_POST`, `TRAFFIC_LIGHT`, `CAMERA`

---

## 6. Comandos do Gateway (TCP)

Todos os comandos são enviados como `ClientRequest.command` (string) via TCP :5009.

| Comando | Campo adicional | Resposta SUCCESS | Resposta ERROR |
|---|---|---|---|
| `PING` | — | `"PONG"` | — |
| `LIST_DEVICES` | — | Lista formatada de dispositivos com `\|`-separadores | `"Nenhum dispositivo conectado."` |
| `GET_AVG` | `target_device_id` | `"Media de 'id': X.XX unit (N leituras)"` | `"Sem leituras para 'id'."` |
| `GET_HISTORY` | — | Últimas 20 leituras em formato `device_id=x \| value=y \| unit=z \| ts=w` | `"Sem leituras ainda."` |
| `SET_STATE` | `target_device_id`, `new_state` (bool) | `"'id' definido como LIGADO/DESLIGADO."` | Atuador offline ou não encontrado |

**Formato de saída de LIST_DEVICES:**
```
N dispositivo(s):
  [1] device_id | tipo=TYPE | atuador=True/False | ip=127.0.0.1:PORT | estado=True/False
```

**Formato de saída de GET_HISTORY (por linha):**
```
device_id=rust_temp_abc | value=24.60 | unit=Celsius | ts=1749123456
```

---

## 7. API REST — Referência de Endpoints

Base URL: `http://localhost:8000`

### Processos

| Método | Path | Descrição |
|---|---|---|
| `GET` | `/processes` | Lista todos os slots e status `running` |
| `POST` | `/processes/{slot}/start` | Inicia o subprocesso do slot |
| `POST` | `/processes/{slot}/stop` | Para o subprocesso (SIGTERM + wait 5 s, SIGKILL se necessário) |
| `GET` | `/processes/{slot}/logs?lines=40` | Últimas N linhas do log do processo |

### Gateway (proxy)

| Método | Path | Descrição |
|---|---|---|
| `GET` | `/gateway/ping` | `{"online": bool}` |
| `GET` | `/gateway/devices` | `{"devices": [...], "count": N}` |
| `GET` | `/gateway/avg/{device_id}` | `{"message": "Media de ..."}` |
| `GET` | `/gateway/readings` | `{"readings": [{device_id, value, unit, ts}, ...]}` |
| `POST` | `/gateway/command` | Body: `{"device_id": "...", "state": true/false}` |

### SSE

| Método | Path | Descrição |
|---|---|---|
| `GET` | `/events` | Stream SSE com eventos a cada 3 s |

---

## 8. SSE — Stream de Eventos em Tempo Real

O frontend se conecta em `GET /events` via `EventSource`. A API emite quatro tipos de evento a cada 3 s:

```
event: processes
data: {"gateway": {"label": "Gateway Inteligente", "running": true}, ...}

event: gateway
data: {"online": true}

event: devices
data: {"devices": [{"device_id": "...", "tipo": "...", "atuador": "False", "ip": "...", "estado": "False"}]}

event: readings
data: {"readings": [{"device_id": "...", "value": "24.60", "unit": "Celsius", "ts": "1749123456"}]}
```

O hook `useSSE` no dashboard detecta mudanças (não repete logs a cada tick):
- Novo `device_id` aparece → log `discovery: id conectado`
- `device_id` some → log `desconexão: id removido`
- Contagem de processos rodando muda → log `processes: N processos rodando`
- Status do gateway muda → log `gateway: Online/Offline`

---

## 9. Decisões de Design

### 9.1 `threading.Lock()` — Prevenção de Race Conditions

Quatro threads acessam `active_devices` e `sensor_history` concorrentemente. Sem lock, `RuntimeError: dictionary changed size during iteration` é inevitável.

**Padrão adotado — snapshot fora do lock:**
```python
with self.lock:
    snapshot = dict(self.active_devices)   # cópia rápida dentro do lock

for did, info in snapshot.items():         # iteração e I/O fora do lock
    lines.append(...)
```
Minimiza o tempo na seção crítica, aumentando a concorrência efetiva.

### 9.2 Lista plana para `sensor_history`

`append()` é O(1) e atômico. Consultas são uma compreensão de lista simples. Dicionário aninhado exigiria `setdefault` + múltiplas operações dentro do lock.

### 9.3 Detecção ativa vs. passiva de falhas

| Tipo | Estratégia | Por quê |
|---|---|---|
| Sensor (UDP) | **Ativa** — timeout de 25 s no monitor | Sensores são silenciosos ao falhar; Gateway nunca os contata por iniciativa própria |
| Atuador (TCP) | **Proba TCP** no monitor (1 s) + **lazy** no SET_STATE | Detecta desconexão em ≤ 10 s sem esperar um comando; lazy como backup no fluxo normal |

**Timeout de 25 s para sensores:** equivale a ~1,7 ciclos de envio (15 s cada), com margem para atrasos transitórios.

### 9.4 Discovery `while True` a cada 5 s

Resolve três cenários que um disparo único não resolveria:
1. Dispositivo ligado **depois** do Gateway
2. Dispositivo que **reiniciou** (perde `gateway_address` da memória)
3. Pacote UDP **descartado** por rede instável

Resultado: sistema **auto-cicatrizante** (self-healing) sem intervenção manual.

### 9.5 `SO_REUSEADDR` no servidor TCP

```python
tcp_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
```
Evita `Address already in use` ao reiniciar o Gateway enquanto a porta 5009 ainda está em `TIME_WAIT`.

### 9.6 `GATEWAY_ADDR` env var

Dispositivos iniciados pela API herdam `GATEWAY_ADDR=127.0.0.1:5008`. Isso pula o bind multicast na porta 5007, que no Windows retorna `WinError 10013` (acesso negado) quando múltiplos processos tentam bindar a mesma porta simultaneamente.

### 9.7 `state` preservado no re-registro

```python
existing = self.active_devices.get(announcement.device_id, {})
self.active_devices[announcement.device_id] = {
    ...
    'state': existing.get('state', False),   # preserva o estado anterior
}
```
Se o Gateway reinicia e um dispositivo re-anuncia, o estado ligado/desligado anterior não é perdido.

---

## 10. Detecção de Falhas e Tolerância

### Sensor desligado (Ctrl+C)

1. Processo termina; socket UDP fecha sem enviar nenhuma mensagem de desconexão
2. `last_seen` para de ser atualizado
3. Em ≤ 25 s, `_thread_monitor_falhas` remove o sensor de `active_devices`
4. Dashboard detecta o desaparecimento no próximo tick SSE e loga `desconexão`

### Atuador desligado (processo morto)

1. Processo termina; porta TCP deixa de aceitar conexões
2. No próximo ciclo do monitor (≤ 10 s), `connect(timeout=1s)` falha com `OSError`
3. Atuador é removido de `active_devices`

### Gateway reiniciado

1. Sensores continuam enviando UDP — pacotes descartados silenciosamente pelo SO
2. Atuadores continuam com TCP server ativo — sem crash
3. Gateway reinicia com `SO_REUSEADDR` → sem erro `Address already in use`
4. Em ≤ 5 s, broadcaster multicast alcança todos os dispositivos
5. Dispositivos re-enviam anúncios → `active_devices` repopulado automaticamente

### Cliente durante queda do Gateway

```python
s.settimeout(5)
# ConnectionRefusedError → mensagem amigável, retorno ao menu
# socket.timeout        → mensagem de timeout, retorno ao menu
```
O cliente nunca trava indefinidamente.

### Comando a atuador offline (lazy detection)

```python
except (ConnectionRefusedError, TimeoutError, OSError):
    self.active_devices.pop(device_id, None)   # remoção imediata
    return "ERROR", f"Atuador '{device_id}' estava offline e foi removido."
```

### Limitação: `sensor_history` em memória

Reiniciar o Gateway apaga todo o histórico de leituras. Não há persistência em banco de dados.

---

## 11. Configuração e Variáveis de Ambiente

| Variável | Onde usada | Efeito |
|---|---|---|
| `GATEWAY_ADDR` | `dispositivos.py`, `main.rs` | `ip:porta` — pula multicast e conecta direto; injetado pela API para todos os slots não-gateway |
| `PYTHONUNBUFFERED` | `api.py` | `"1"` — flush imediato nos logs dos subprocessos Python |

**Constantes de rede (hardcoded, sem env var):**

| Constante | Valor | Arquivo |
|---|---|---|
| `MULTICAST_GROUP` | `224.1.1.1` | gateway.py, dispositivos.py |
| `MULTICAST_PORT` | `5007` | gateway.py, dispositivos.py |
| `GATEWAY_DATA_PORT` | `5008` | gateway.py |
| `GATEWAY_TCP_PORT` | `5009` | gateway.py |
| `DISCOVERY_INTERVAL` | `5` s | gateway.py |
| `TIMEOUT_SENSOR` | `25` s | gateway.py (_thread_monitor_falhas) |
| Monitor interval | `10` s | gateway.py (_thread_monitor_falhas) |
| Sensor send interval | `15` s | dispositivos.py, main.rs |
| API port | `8000` | api.py |
| Dashboard port | `3000` | Next.js padrão |

---

## 12. Guia de Execução

### Pré-requisitos

```bash
pip install protobuf fastapi "uvicorn[standard]"
# Rust (para o sensor opcional): winget install Rustlang.Rustup
# Node.js 18+ para o dashboard
```

### Com dashboard

```bash
# Terminal 1
python api.py

# Terminal 2
cd dashboard && npm install && npm run dev
# Acessar http://localhost:3000
```

### Sem dashboard (linha de comando)

```bash
# Gateway primeiro
python gateway.py

# Dispositivos (em terminais separados)
python -c "from dispositivos import Continuos; Continuos('TEMPERATURE_SENSOR','Celsius').iniciar()"
python -c "from dispositivos import SensorControlavel; SensorControlavel('AIR_QUALITY_SENSOR','ug/m3').iniciar()"
python -c "from dispositivos import Atuador; Atuador('LAMP_POST').iniciar()"

# Sensor Rust (opcional)
cd dispositivo_rust && cargo run

# Cliente analítico
python cliente.py
```

### Simulação de falhas

| Cenário | Como simular | O que observar |
|---|---|---|
| Sensor offline | Ctrl+C no processo do sensor | Em ≤ 25 s: alerta no Gateway, dispositivo some do LIST_DEVICES |
| Atuador offline | Ctrl+C no processo do atuador | Em ≤ 10 s: removed pelo monitor; ou na próxima SET_STATE: erro lazy |
| Gateway reiniciado | Ctrl+C + relançar gateway.py | Em ≤ 5 s: todos os dispositivos voltam a aparecer |
| Cliente sem gateway | Rodar cliente.py sem gateway | Mensagem de erro, retorna ao menu — sem crash |

---

*Baseada na análise dos arquivos: `gateway.py`, `dispositivos.py`, `cliente.py`, `api.py`, `protos/todolist.proto`, `dispositivo_rust/src/main.rs`, `dashboard/hooks/useSSE.ts`, `dashboard/lib/api.ts`, `dashboard/app/page.tsx`.*

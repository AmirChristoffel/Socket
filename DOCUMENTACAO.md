# Documentação Técnica — Gateway Inteligente: Cidade Inteligente (SD)

> **Disciplina:** Sistemas Distribuídos  
> **Objetivo:** Base de estudo para defesa oral — arquitetura, decisões de design e resiliência do sistema.

---

## Índice

1. [Visão Geral do Sistema](#1-visão-geral-do-sistema)
2. [Arquitetura de Rede e Mensageria](#2-arquitetura-de-rede-e-mensageria)
3. [Decisões de Design — O "Porquê"](#3-decisões-de-design--o-porquê)
4. [Resiliência e Tratamento de Falhas](#4-resiliência-e-tratamento-de-falhas)
5. [Guia de Execução Passo a Passo](#5-guia-de-execução-passo-a-passo)
6. [Sensor de Temperatura em Rust](#6-sensor-de-temperatura-em-rust)

---

## 1. Visão Geral do Sistema

### Objetivo

O projeto implementa um sistema IoT distribuído para uma **Cidade Inteligente**, onde dispositivos heterogêneos (sensores de temperatura, qualidade do ar, câmeras, postes, semáforos) se registram automaticamente em um Gateway centralizado. Um Cliente Analítico pode, a qualquer momento, consultar o estado da rede e enviar comandos de controle — tudo isso sem configuração manual de endereços.

### Componentes Principais

| Componente | Arquivo | Papel |
|---|---|---|
| **Gateway Inteligente** | `gateway.py` | Cérebro central: descobre dispositivos, armazena leituras, atende o cliente analítico |
| **Sensor Contínuo** | `dispositivos.py` → `Continuos` | Envia leituras periódicas via UDP; não aceita comandos |
| **Atuador** | `dispositivos.py` → `Atuador` | Recebe comandos TCP (ligar/desligar); não envia dados |
| **Sensor Controlável** | `dispositivos.py` → `SensorControlavel` | Híbrido: envia leituras UDP **e** aceita comandos TCP (ex.: ajuste de threshold) |
| **Cliente Analítico** | `cliente.py` | Interface do operador: lista dispositivos, consulta médias, envia comandos |
| **Sensor de Temperatura (Rust)** | `dispositivo_rust/src/main.rs` | Sensor contínuo implementado em Rust; demonstra interoperabilidade de linguagens via Protobuf |

### Hierarquia de Classes dos Dispositivos

```
Dispositivos (base)
│
├── Atuador           ← is_actuator = True  | aceita TCP, não envia dados
│
└── Continuos         ← is_actuator = False | envia UDP, não aceita TCP
    │
    └── SensorControlavel ← is_actuator = True | envia UDP + aceita TCP (híbrido)
```

---

## 2. Arquitetura de Rede e Mensageria

### Mapa de Protocolos e Portas

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GATEWAY INTELIGENTE                          │
│                          (gateway.py)                               │
│                                                                     │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────────┐  │
│  │ Thread Discovery │  │  Thread UDP Recv  │  │  Thread TCP Server│  │
│  │   Broadcaster    │  │  (anúncios+dados) │  │ (cliente analítico│  │
│  │   Porta 5007     │  │    Porta 5008     │  │    Porta 5009)    │  │
│  └────────┬─────────┘  └────────▲──────────┘  └────────▲──────────┘  │
│           │ UDP Multicast        │ UDP Unicast           │ TCP         │
└───────────┼──────────────────────┼───────────────────────┼────────────┘
            │                      │                       │
    ┌───────▼──────────┐   ┌───────┴──────────┐   ┌───────┴──────────┐
    │  TODOS os        │   │  Dispositivos    │   │  Cliente         │
    │  Dispositivos    │   │  (anúncio +      │   │  Analítico       │
    │  (grupo multicast│   │   sensor data)   │   │  (cliente.py)    │
    │  224.1.1.1:5007) │   │  → 127.0.0.1:5008│   │  → 127.0.0.1:5009│
    └──────────────────┘   └──────────────────┘   └──────────────────┘
```

### 2.1 UDP Multicast — Descoberta (Porta 5007)

**O que é:** O Gateway envia periodicamente uma mensagem `GatewayDiscovery` para o grupo multicast `224.1.1.1:5007`. Todos os dispositivos que fazem parte desse grupo recebem a mensagem simultaneamente, sem que o Gateway precise conhecer o endereço de nenhum deles.

**Por que UDP Multicast?**

- **1 → N sem overhead:** Uma única transmissão do Gateway alcança todos os dispositivos presentes na rede local. Com TCP ou UDP Unicast, seria necessário manter uma lista de endereços previamente conhecidos — impossível em um ambiente dinâmico onde dispositivos entram e saem a qualquer momento.
- **Baixa latência e sem conexão:** A descoberta não requer que o dispositivo esteja "esperando" uma conexão específica — basta pertencer ao grupo multicast.
- **TTL = 2:** O campo `IP_MULTICAST_TTL` foi configurado para 2 saltos de roteador, confinando a descoberta à rede local e evitando propagação desnecessária.

**Fluxo:**
```
Gateway → sendto(224.1.1.1:5007, GatewayDiscovery{data_port=5008})
Dispositivo ← recvfrom → extrai gateway_ip + data_port → envia anúncio para 5008
```

**Trecho de código relevante (`gateway.py:57-71`):**
```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
msg.discovery.data_port = GATEWAY_DATA_PORT  # comunica onde se registrar
sock.sendto(serialized, (MULTICAST_GROUP, MULTICAST_PORT))
```

---

### 2.2 UDP Unicast — Envio Contínuo de Sensores (Porta 5008)

**O que é:** Após a descoberta, cada sensor envia suas leituras periódicas diretamente para o endereço IP do Gateway na porta 5008 (UDP Unicast ponto-a-ponto). Essa mesma porta também recebe os anúncios iniciais dos dispositivos.

**Por que UDP Unicast para os dados dos sensores?**

- **Dados são descartáveis:** Uma leitura de temperatura ou qualidade do ar que se perde na rede é simplesmente substituída pela próxima leitura em 15 segundos. Não há prejuízo funcional em perder um pacote isolado.
- **Sem overhead de conexão:** TCP exigiria manter uma conexão persistente aberta por cada sensor — potencialmente dezenas ou centenas de conexões simultâneas no Gateway. UDP é stateless; o Gateway não precisa gerenciar nenhum estado de conexão por sensor.
- **Throughput:** Para telemetria de alta frequência com múltiplos dispositivos, o overhead dos headers TCP (ACK, controle de congestionamento, etc.) seria desperdiçado em pacotes pequenos e tolerantes à perda.
- **Porta unificada (5008):** A mesma porta recebe tanto anúncios (`DeviceAnnouncement`) quanto dados (`SensorData`). O campo `oneof payload` do Protobuf permite ao Gateway rotear a mensagem para o handler correto sem precisar de portas separadas.

---

### 2.3 TCP — Comandos e Controle (Porta 5009 e portas dinâmicas)

**O que é:** Dois usos distintos de TCP no sistema:

1. **Gateway ↔ Cliente Analítico (porta 5009):** Conexão sob demanda — o Cliente abre um socket TCP, envia um `ClientRequest`, recebe a `GatewayResponse` e fecha a conexão.
2. **Gateway → Atuador/SensorControlavel (porta dinâmica):** O Gateway abre uma conexão TCP direto ao dispositivo para entregar um `ActuatorCommand`.

**Por que TCP para comandos?**

- **Confiabilidade é obrigatória:** Um comando "Desligar semáforo" não pode ser perdido silenciosamente. O TCP garante entrega via retransmissão automática e confirmação de recebimento (ACK).
- **Ordem preservada:** O TCP garante que os bytes chegam na ordem em que foram enviados — crítico para mensagens Protobuf binárias que não têm delimitador natural entre campos.
- **Detecção de falha integrada:** Se a conexão TCP falhar (dispositivo offline), o Gateway recebe `ConnectionRefusedError` imediatamente e pode tomar ação — diferente do UDP onde a perda é silenciosa.

**Porta dinâmica nos dispositivos:** Cada dispositivo faz `bind(('127.0.0.1', 0))` — o SO aloca a porta disponível automaticamente. A porta real é capturada com `getsockname()[1]` e incluída no anúncio `DeviceAnnouncement.port`, para que o Gateway saiba onde se conectar.

---

### 2.4 Protocol Buffers (Protobuf) — Mensageria Binária

**Por que Protobuf em vez de JSON ou texto puro?**

| Critério | Texto puro | JSON | **Protobuf** |
|---|---|---|---|
| Tamanho do payload | Grande | Médio | **Mínimo** (binário, sem chaves textuais) |
| Velocidade de serialização | Lenta | Média | **Rápida** |
| Tipagem | Nenhuma | Fraca | **Forte** (schema `.proto` compilado) |
| Evolução do schema | Manual | Manual | **Versionado** (campos numerados) |
| Legibilidade humana | Alta | Alta | Baixa (binário) |
| Validação automática | Não | Parcial | **Sim** (tipos garantidos pelo compilador) |

**O padrão Envelope (`SmartCityMessage`):** O campo `oneof payload` permite que um único tipo de socket trafegue qualquer mensagem do sistema. O receptor verifica qual campo está preenchido com `HasField()` e roteia para o handler adequado — sem precisar inspecionar o conteúdo do payload ou manter sockets separados por tipo de mensagem.

```protobuf
message SmartCityMessage {
  oneof payload {
    GatewayDiscovery    discovery      = 1;
    DeviceAnnouncement  announcement   = 2;
    SensorData          sensor_data    = 3;
    ActuatorCommand     command        = 4;
    ClientRequest       client_request = 5;
    GatewayResponse     gateway_response = 6;
  }
}
```

---

## 3. Decisões de Design — O "Porquê"

### 3.1 `threading.Lock()` e a Prevenção de Race Conditions

**O problema:** O Gateway roda quatro threads concorrentes que acessam os mesmos dados:

| Thread | Operação |
|---|---|
| `UDP-Receiver` | Escreve em `active_devices` e `sensor_history` |
| `Fault-Monitor` | Lê e deleta de `active_devices` |
| `TCP-Server` (por cliente) | Lê `active_devices` e `sensor_history`; pode deletar de `active_devices` |
| `Discovery-Broadcaster` | Apenas leitura de configurações imutáveis — não precisa de lock |

**O que é uma Race Condition?** É quando o resultado de uma operação depende da ordem não-determinística em que múltiplas threads acessam um recurso compartilhado. Em Python, mesmo uma expressão simples como `dict[key] = value` pode ser interrompida pelo GIL (Global Interpreter Lock) em um momento arbitrário.

**Cenário catastrófico sem Lock:**

```
Thread Fault-Monitor:  for device_id in list(active_devices.keys()):
                                    ← INTERRUPÇÃO DO SCHEDULER →
Thread UDP-Receiver:   active_devices["novo_sensor"] = {...}  # insere chave nova
Thread Fault-Monitor:  del active_devices[device_id]  # itera sobre estado inconsistente
                       → RuntimeError: dictionary changed size during iteration
```

**Como o Lock resolve:** `threading.Lock()` é um mutex binário. O bloco `with self.lock:` garante que apenas uma thread por vez executa a seção crítica. As demais ficam bloqueadas na entrada do `with` até o lock ser liberado.

**Padrão adotado — snapshot + processamento fora do lock:**
```python
# DENTRO do lock: apenas a cópia rápida do estado
with self.lock:
    snapshot = dict(self.active_devices)  # O(n) — rápido

# FORA do lock: formatação, I/O, etc. — sem bloquear outras threads
for did, info in snapshot.items():
    lines.append(f"  [{i+1}] {did} | ...")
```
Isso minimiza o tempo que o lock é mantido, aumentando a concorrência efetiva do sistema.

---

### 3.2 Lista Plana para `sensor_history` em vez de Dicionários Aninhados

**Alternativa descartada (dicionário aninhado):**
```python
# NÃO usado — por quê?
sensor_history = {
    "sensor_abc": [
        {"value": 23.5, "unit": "Celsius", "timestamp": 1717000000},
        ...
    ]
}
```

**Escolha feita (lista plana):**
```python
sensor_history = [
    {"device_id": "sensor_abc", "value": 23.5, "unit": "Celsius", "timestamp": ...},
    {"device_id": "sensor_xyz", "value": 45.1, "unit": "µg/m³",   "timestamp": ...},
    ...
]
```

**Razões técnicas para a lista plana:**

1. **Simplicidade de append:** Toda nova leitura é `self.sensor_history.append(entry)` — O(1) independente do número de dispositivos. Com dicionário aninhado, seria necessário primeiro verificar se a chave existe, criar a lista se não existir (`setdefault`), e então fazer o append — mais propenso a erros.

2. **Consulta por `GET_AVG` é uma única compreensão de lista:**
   ```python
   readings = [r for r in self.sensor_history if r['device_id'] == device_id]
   ```
   Com dicionário aninhado seria `sensor_history.get(device_id, [])` — mais rápido para lookup, mas a lista plana foi suficiente para o escopo do projeto e mantém a estrutura uniforme.

3. **Facilidade de extensão:** Uma lista plana é trivialmente serializável para CSV, banco de dados ou streaming — cada entrada é um registro independente e completo, sem dependência da estrutura hierárquica.

4. **Sem sincronização complexa:** Com dicionário aninhado, múltiplas operações (verificar chave, criar lista, appender) precisariam ser atômicas dentro do lock. A lista plana reduz o trabalho dentro da seção crítica a uma única operação.

---

### 3.3 Estratégias Diferentes de Tolerância a Falhas: Ativa (Heartbeat) vs. Passiva (Lazy)

Esta é uma das decisões mais sofisticadas do sistema. Dois tipos de dispositivos recebem tratamentos completamente diferentes quando falham.

#### Detecção Ativa para Sensores — Heartbeat/Timeout de 35 segundos

**Onde:** `_thread_monitor_falhas()` em `gateway.py:291-321`

**Como funciona:** Uma thread verifica a cada 10 segundos se algum sensor não enviou dados há mais de 35 segundos. Se o `last_seen` estiver vencido, o sensor é removido de `active_devices`.

```python
TIMEOUT_SENSOR = 35  # 15s de intervalo de envio + 20s de margem
if not info['is_actuator'] and tempo_inativo > TIMEOUT_SENSOR:
    del self.active_devices[device_id]
```

**Por que 35 segundos?** Os sensores enviam dados a cada 15 segundos. O timeout de 35 segundos equivale a 2,3 ciclos de envio — margem suficiente para absorver um atraso de rede ou a lentidão momentânea do sistema operacional, mas pequena o suficiente para detectar falhas reais rapidamente.

**Por que detectar ativamente sensores?** Sensores são **silenciosos por natureza quando falham** — simplesmente param de enviar pacotes UDP. Não há conexão TCP que gere um `ConnectionRefusedError`. O Gateway nunca "tentará contatar" um sensor por iniciativa própria, portanto a única forma de saber que ele morreu é perceber a ausência de suas mensagens.

#### Detecção Passiva (Lazy) para Atuadores

**Onde:** `_cmd_set_state()` em `gateway.py:254-285`

**Como funciona:** O Gateway só descobre que um atuador está offline quando tenta enviar um comando TCP a ele e recebe `ConnectionRefusedError` ou `TimeoutError`. Nesse momento, remove o atuador de `active_devices`.

```python
try:
    with socket.socket(...) as s:
        s.settimeout(5)
        s.connect((device['ip'], device['port']))  # ← falha aqui se offline
        s.sendall(cmd_msg.SerializeToString())
except (ConnectionRefusedError, TimeoutError, OSError):
    with self.lock:
        self.active_devices.pop(device_id, None)   # ← remoção lazy
```

**Por que detectar passivamente atuadores?** Atuadores são **passivos por natureza** — ficam apenas aguardando comandos TCP. Um monitor de heartbeat para atuadores exigiria que o Gateway iniciasse uma conexão de "ping" periódica para cada um — gerando tráfego desnecessário e complexidade adicional.

Além disso, a falha de um atuador só importa no momento em que alguém tenta controlá-lo. Não faz sentido manter um ciclo de monitoramento constante para detectar antecipadamente uma falha que só será relevante "se e quando" um operador emitir um comando.

**Comparativo visual:**

| Critério | Sensor (Ativa) | Atuador (Passiva) |
|---|---|---|
| Comportamento normal | Envia dados periodicamente | Fica aguardando passivamente |
| Como a falha se manifesta | Para de enviar UDP | Recusa conexão TCP |
| Quando a falha importa | Sempre (dados faltando = problema) | Só quando um comando é enviado |
| Overhead de monitoramento | Uma thread + lock a cada 10s | Zero — detecção acontece no fluxo normal |

---

### 3.4 Por que o Discovery Multicast usa `while True` em vez de disparar uma única vez?

**Trecho (`gateway.py:68-71`):**
```python
while True:
    sock.sendto(serialized, (MULTICAST_GROUP, MULTICAST_PORT))
    time.sleep(DISCOVERY_INTERVAL)  # 5 segundos
```

**Cenários que o `while True` resolve e um disparo único não resolveria:**

1. **Dispositivos que entram na rede depois do Gateway iniciar:** Se o Gateway enviasse a mensagem de descoberta apenas uma vez ao iniciar, qualquer sensor ou atuador ligado após esse momento jamais receberia o endereço do Gateway e nunca se registraria.

2. **Dispositivos que reiniciam:** Um sensor que sofre um reboot perde seu `gateway_address` da memória. Ele voltará a escutar o grupo multicast e só conseguirá se registrar novamente quando o Gateway enviar o próximo broadcast.

3. **Falhas de rede transitórias:** Um pacote UDP pode ser descartado por um switch sobrecarregado. Com envio periódico, o próximo ciclo de 5 segundos garante uma nova oportunidade de descoberta.

**Por que 5 segundos?** É um compromisso entre:
- **Responsividade:** Um dispositivo novo se registra em no máximo 5 segundos após ligar.
- **Overhead de rede:** Um broadcast multicast a cada 5 segundos gera tráfego desprezível (mensagem Protobuf de ~10 bytes).

**Auto-reparo da rede:** Essa decisão é o que torna o sistema **auto-cicatrizante** (self-healing). A rede se reconstrói sozinha após qualquer falha sem intervenção manual — característica essencial de sistemas distribuídos robustos.

---

## 4. Resiliência e Tratamento de Falhas

### 4.1 Cenário: Sensor Desligado Abruptamente (Ctrl+C)

**O que acontece na rede:**

1. O processo do sensor termina. O socket UDP é fechado pelo SO — mas como UDP é sem conexão, **nenhuma mensagem de "desconexão" é enviada ao Gateway**. O Gateway simplesmente para de receber pacotes UDP daquele sensor.

2. O campo `last_seen` do sensor em `active_devices` para de ser atualizado.

3. **Em até 35 segundos**, a thread `Fault-Monitor` detecta que `tempo_inativo > TIMEOUT_SENSOR` e remove o dispositivo:
   ```
   [ALERTA] Sensor 'temperature_sensor_a1b2' parou de enviar dados e foi removido!
   ```

4. A partir desse momento, consultas `GET_AVG` retornam `"ERROR: Sem leituras para 'sensor_id'"` — os dados históricos permanecem em `sensor_history` mas o dispositivo sai do `LIST_DEVICES`.

5. **Quando o sensor reiniciar**, ele ouvirá o próximo broadcast multicast (em até 5 segundos), enviará um anúncio com novo ID (UUID regenerado) e voltará a aparecer no `LIST_DEVICES` como um novo dispositivo.

---

### 4.2 Cenário: Gateway Reiniciado

**O que acontece na rede:**

1. Todos os sockets do Gateway fecham. As threads daemon encerram automaticamente (por serem `daemon=True`).

2. Os sensores continuam tentando enviar UDP para `127.0.0.1:5008` — os pacotes são descartados silenciosamente pelo SO (nenhum processo escuta a porta). Nenhum sensor crashar — UDP é fire-and-forget.

3. Os atuadores ficam aguardando conexões TCP — também não crasham.

4. **Quando o Gateway reinicia:**
   - `SO_REUSEADDR` na porta TCP 5009 evita o erro `Address already in use` que ocorreria enquanto a porta está em TIME_WAIT.
   - O broadcaster multicast começa imediatamente a enviar descobertas a cada 5 segundos.
   - Em até 5 segundos, todos os sensores e atuadores ativos recebem a mensagem de descoberta e re-enviam seus anúncios.
   - O `active_devices` se repopula. A rede está operacional novamente em **menos de 10 segundos** sem qualquer intervenção manual.

5. **Dado perdido:** O `sensor_history` é mantido apenas em memória — ao reiniciar o Gateway, todo o histórico de leituras é perdido. Isso é uma limitação do sistema atual (sem persistência em banco de dados).

---

### 4.3 Cenário: Gateway Cai Durante uma Consulta do Cliente

**No `cliente.py`:**

```python
try:
    with socket.socket(...) as s:
        s.settimeout(5)          # ← timeout de 5 segundos
        s.connect((GATEWAY_IP, GATEWAY_TCP_PORT))
        ...
except ConnectionRefusedError:
    print("[ERRO] Gateway não está acessível.")
    return None
except socket.timeout:
    print("[ERRO] Timeout: o Gateway não respondeu em 5 segundos.")
    return None
except Exception as e:
    print(f"[ERRO] Falha na comunicação: {e}")
    return None
```

**Comportamento:**
- Se o Gateway estiver offline, `connect()` lança `ConnectionRefusedError` imediatamente (< 1ms). O cliente imprime uma mensagem amigável e retorna ao menu.
- Se o Gateway estiver lento ou travado, o `settimeout(5)` garante que o cliente nunca ficará bloqueado indefinidamente.
- **O cliente nunca crasha** — todas as exceções são capturadas e tratadas graciosamente.
- O loop principal do menu continua rodando, permitindo que o operador tente novamente após o Gateway voltar.

---

### 4.4 Cenário: Comando Enviado a Atuador Offline

**No `gateway.py` — `_cmd_set_state()`:**

```python
except (ConnectionRefusedError, TimeoutError, OSError):
    with self.lock:
        self.active_devices.pop(device_id, None)
    print(f"\n[ALERTA] Atuador '{device_id}' não respondeu e foi removido!\n")
    return "ERROR", f"Atuador '{device_id}' estava offline e foi removido."
```

**Comportamento:**
1. O Gateway tenta conectar TCP ao atuador com timeout de 5 segundos.
2. Se falhar, o atuador é **imediatamente removido** de `active_devices` (detecção lazy).
3. O Cliente recebe uma resposta `ERROR` com mensagem explicativa.
4. Próxima consulta `LIST_DEVICES` já não mostrará o atuador removido.

---

## 5. Guia de Execução Passo a Passo

### Pré-requisitos

```bash
# Instalar dependências Python
pip install protobuf

# Verificar que o arquivo compilado existe
# protos/todolist_pb2.py deve estar presente
```

### Estrutura de Arquivos Esperada

```
SD_Socktes/
├── gateway.py
├── dispositivos.py
├── cliente.py
├── protos/
│   ├── todolist.proto
│   └── todolist_pb2.py   ← gerado pelo compilador protoc
└── dispositivo_rust/
    ├── Cargo.toml
    ├── build.rs           ← compila o .proto em tempo de build
    └── src/
        └── main.rs        ← sensor de temperatura em Rust
```

### Compilar o Protobuf (se necessário)

```bash
# Na raiz do projeto
protoc --python_out=. protos/todolist.proto
```

---

### Roteiro de Demonstração

#### Terminal 1 — Iniciar o Gateway (SEMPRE PRIMEIRO)

```bash
python gateway.py
```

**Saída esperada:**
```
====================================================
   Gateway Inteligente — Cidade Inteligente
====================================================
[UDP] Aguardando mensagens na porta 5008 (anúncios + dados)
[TCP] Servidor escutando na porta 5009 (Cliente Analitico)
[Monitor] Thread de deteccao de falhas iniciada (intervalo=10s).
[Discovery] Broadcaster iniciado → 224.1.1.1:5007
[Discovery] Dispositivos devem responder na porta UDP 5008
[Gateway] Em execucao. Pressione Ctrl+C para sair.
```

> **Por que o Gateway primeiro?** Os dispositivos precisam receber o broadcast multicast para saber onde se registrar. Se iniciados antes do Gateway, eles ouvirão o próximo broadcast (em até 5 segundos) e se registrarão automaticamente — mas começar pelo Gateway é mais didático.

---

#### Terminal 2 — Iniciar um Sensor de Temperatura

```python
# Crie um arquivo run_sensor_temp.py
from dispositivos import Continuos
sensor = Continuos(tipo="TEMPERATURE_SENSOR", data_unit="Celsius")
sensor.iniciar()
```

```bash
python run_sensor_temp.py
```

**Saída esperada no Terminal 2:**
```
Iniciando sensor: temperature_sensor_a1b2
[temperature_sensor_a1b2] Aguardando descoberta em 224.1.1.1:5007
[temperature_sensor_a1b2] Aguardando descoberta do Gateway para iniciar envio...

[temperature_sensor_a1b2] Gateway descoberto em ('127.0.0.1', 5008)
[temperature_sensor_a1b2] Anúncio enviado para o Gateway.
[temperature_sensor_a1b2] Gateway encontrado. Enviando dados para ('127.0.0.1', 5008)
[temperature_sensor_a1b2] Nova leitura: 27.43 Celsius
```

**Saída esperada no Terminal 1 (Gateway):**
```
[UDP] + Dispositivo registrado: temperature_sensor_a1b2
      Tipo: TEMPERATURE_SENSOR  |  Atuador: False  |  Endereco: 127.0.0.1:XXXX

[UDP] [sensor] Leitura de 'temperature_sensor_a1b2': 27.43 Celsius
```

---

#### Terminal 3 — Iniciar um Sensor de Qualidade do Ar (Controlável)

```python
# Crie run_sensor_ar.py
from dispositivos import SensorControlavel
sensor = SensorControlavel(tipo="AIR_QUALITY_SENSOR", data_unit="µg/m³")
sensor.iniciar()
```

```bash
python run_sensor_ar.py
```

---

#### Terminal 4 — Iniciar um Atuador (Poste de Luz)

```python
# Crie run_atuador.py
from dispositivos import Atuador
atuador = Atuador(tipo="LAMP_POST")
atuador.iniciar()
```

```bash
python run_atuador.py
```

---

#### Terminal 5 — Iniciar o Cliente Analítico (POR ÚLTIMO)

```bash
python cliente.py
```

**Menu exibido:**
```
============================================
   Cliente Analitico — Cidade Inteligente
============================================
  [1] Listar dispositivos online
  [2] Consultar media de um sensor
  [3] Ligar / Desligar atuador
  [0] Sair
--------------------------------------------
Escolha uma opcao:
```

---

### Sequência de Comandos para Demonstração

#### 1. Listar Dispositivos Online (`opção 1`)
```
Escolha uma opcao: 1

3 dispositivo(s):
  [1] temperature_sensor_a1b2 | tipo=TEMPERATURE_SENSOR | atuador=False | ip=127.0.0.1:XXXX
  [2] air_quality_sensor_c3d4 | tipo=AIR_QUALITY_SENSOR | atuador=True  | ip=127.0.0.1:YYYY
  [3] lamp_post_e5f6          | tipo=LAMP_POST           | atuador=True  | ip=127.0.0.1:ZZZZ
```

#### 2. Consultar Média de um Sensor (`opção 2`)
```
Escolha uma opcao: 2
  Digite o ID do sensor: temperature_sensor_a1b2

  Media de 'temperature_sensor_a1b2': 26.87 Celsius (4 leituras)
```

#### 3. Ligar um Atuador (`opção 3`)
```
Escolha uma opcao: 3
  Digite o ID do atuador: lamp_post_e5f6
  Novo estado [1=Ligar / 0=Desligar]: 1

  'lamp_post_e5f6' definido como LIGADO.
```

---

### Simulação de Falhas

#### Derrubar um Sensor (Ctrl+C no Terminal 2)
Aguardar até 35 segundos e observar no **Terminal 1**:
```
[ALERTA] Sensor 'temperature_sensor_a1b2' parou de enviar dados e foi removido!
```

#### Reiniciar o Gateway (Ctrl+C no Terminal 1, depois relançar)
Observar nos terminais dos dispositivos: eles continuam rodando. Após relançar o Gateway, em até 5 segundos todos voltam a aparecer em `LIST_DEVICES`.

#### Derrubar um Atuador e Tentar Comandar
Com o Terminal 4 encerrado, ir ao Cliente e tentar `SET_STATE` no atuador morto:
```
  'lamp_post_e5f6' estava offline e foi removido.
```

---

## Apêndice — Tabela Resumo dos Protocolos

| Protocolo | Porta | Direção | Tipo de Mensagem | Justificativa |
|---|---|---|---|---|
| UDP Multicast | 5007 | Gateway → Todos | `GatewayDiscovery` | Descoberta 1→N sem endereços pré-conhecidos |
| UDP Unicast | 5008 | Dispositivos → Gateway | `DeviceAnnouncement` | Registro inicial após descoberta |
| UDP Unicast | 5008 | Sensores → Gateway | `SensorData` | Telemetria tolerante à perda |
| TCP | 5009 | Cliente → Gateway | `ClientRequest` / `GatewayResponse` | Comandos confiáveis do operador |
| TCP | Dinâmica | Gateway → Atuador | `ActuatorCommand` | Controle confiável de dispositivos |

---

---

## 6. Sensor de Temperatura em Rust

### Objetivo

O `dispositivo_rust` implementa o mesmo papel de um `Sensor Contínuo` Python — mas em **Rust**. Seu propósito principal é demonstrar que o sistema é **agnóstico de linguagem**: qualquer processo que fale o mesmo protocolo (UDP + Protobuf) integra-se ao Gateway sem modificações, independentemente da linguagem em que foi escrito.

### O que o sensor faz

1. **Entra no grupo Multicast** `224.1.1.1:5007` e aguarda o broadcast de descoberta do Gateway.
2. **Ao detectar o Gateway**, envia um `DeviceAnnouncement` identificando-se como `TEMPERATURE_SENSOR` via UDP Unicast.
3. **Inicia o envio de telemetria** a cada 15 segundos: temperatura simulada entre 20 °C e 30 °C, oscilando com base no timestamp Unix.
4. **Se a conexão ao Gateway falhar**, retorna ao loop de descoberta e aguarda o próximo broadcast.

### Pré-requisitos

| Ferramenta | Instalação |
|---|---|
| Rust (rustc + cargo) | `winget install Rustlang.Rustup` (reiniciar o terminal após) |
| Compilador Protobuf (`protoc`) | `winget install Google.Protobuf` |

> O `protoc` é necessário porque o `build.rs` compila o arquivo `.proto` automaticamente durante o `cargo build`.

### Como executar

```powershell
# Em um terminal separado, com o Gateway já rodando
cd dispositivo_rust
cargo run
```

O `cargo` baixa todas as dependências automaticamente na primeira execução. A compilação inicial demora ~1 minuto; execuções subsequentes são instantâneas.

### Saída esperada

```
=== Dispositivo IoT em Rust Iniciado ===
Device ID: rust_temp_sensor_71b4
[Multicast] Aguardando broadcast de descoberta do Gateway (porta 5007)...

[Discovery] Gateway detectado em 127.0.0.1:5007
[Discovery] Porta de dados do Gateway: 5008
[Registro] Anuncio de dispositivo enviado para o Gateway.
[Telemetria] Iniciando envio de leituras a cada 15 segundos...
[Telemetria] Enviado: rust_temp_sensor_71b4 = 24.6 Celsius (timestamp: 1749123456)
[Telemetria] Enviado: rust_temp_sensor_71b4 = 24.7 Celsius (timestamp: 1749123471)
```

No **Terminal do Gateway**, o sensor aparece como qualquer outro dispositivo:

```
[UDP] + Dispositivo registrado: rust_temp_sensor_71b4
      Tipo: TEMPERATURE_SENSOR  |  Atuador: False  |  Endereco: 127.0.0.1:XXXX

[UDP] [sensor] Leitura de 'rust_temp_sensor_71b4': 24.6 Celsius
```

### Por que Rust demonstra interoperabilidade?

O Gateway não tem nenhum conhecimento de que o sensor é escrito em Rust. Ele recebe um pacote UDP binário, desserializa com Protobuf e processa normalmente. O mesmo vale para qualquer outra linguagem (C, Go, Java, etc.) que implemente o mesmo schema `.proto`. Isso ilustra uma propriedade fundamental de sistemas distribuídos: **o contrato é o protocolo, não a implementação**.

---

*Documentação gerada com base na análise dos arquivos: `gateway.py`, `dispositivos.py`, `cliente.py`, `protos/todolist.proto` e `dispositivo_rust/src/main.rs`.*

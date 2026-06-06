# Gateway Inteligente — Cidade Inteligente (SD)

Sistema IoT distribuído onde dispositivos heterogêneos se registram automaticamente em um Gateway centralizado. Um Cliente Analítico consulta o estado da rede e envia comandos de controle sem configuração manual de endereços.

---

## Componentes

| Componente | Arquivo | Papel |
|---|---|---|
| **Gateway** | `gateway.py` | Cérebro: descobre dispositivos, armazena leituras, atende o cliente |
| **Sensor Contínuo** | `dispositivos.py → Continuos` | Envia leituras UDP periódicas; não aceita comandos |
| **Atuador** | `dispositivos.py → Atuador` | Recebe comandos TCP (ligar/desligar); não envia dados |
| **Sensor Controlável** | `dispositivos.py → SensorControlavel` | Híbrido: envia UDP **e** aceita TCP (ajuste de threshold) |
| **Cliente Analítico** | `cliente.py` | Lista dispositivos, consulta médias, envia comandos |

**Hierarquia:** `Dispositivos` → `Atuador` / `Continuos` → `SensorControlavel`

---

## Arquitetura de Rede

```
Gateway (broadcaster) ──UDP Multicast 5007──► Todos os dispositivos
Dispositivos          ──UDP Unicast    5008──► Gateway (anúncio + sensor data)
Cliente Analítico     ──TCP            5009──► Gateway (comandos / respostas)
Gateway               ──TCP   porta dinâmica► Atuador / SensorControlavel
```

| Protocolo | Porta | Por quê |
|---|---|---|
| UDP Multicast | 5007 | Descoberta 1→N sem endereços pré-conhecidos; TTL=2 (rede local) |
| UDP Unicast | 5008 | Telemetria tolerante à perda; sem overhead de conexão por sensor |
| TCP | 5009 | Comandos do cliente exigem entrega garantida e ordem preservada |
| TCP | Dinâmica | Controle confiável de atuadores; porta anunciada no `DeviceAnnouncement` |

**Protobuf vs JSON:** binário (menor payload), tipagem forte, schema versionado, validação automática. O padrão `oneof SmartCityMessage` roteia qualquer tipo de mensagem pelo mesmo socket via `HasField()`.

---

## Decisões de Design

**`threading.Lock()`** — 4 threads acessam `active_devices` e `sensor_history` concorrentemente. Sem lock, `dict changed size during iteration` é inevitável. Padrão adotado: copia o estado para um snapshot *dentro* do lock e faz I/O/formatação *fora*, minimizando o tempo da seção crítica.

**Lista plana para `sensor_history`** — `append()` é O(1) e atômico; a compreensão de lista em `GET_AVG` filtra por `device_id` em uma única expressão. Dicionário aninhado exigiria `setdefault` + múltiplas operações atômicas dentro do lock.

**Heartbeat ativo (35 s) para sensores** — sensores falhos ficam silenciosos; o Gateway nunca tenta contatá-los, então a única detecção possível é pela ausência de mensagens. Timeout = 2,3× o intervalo de envio (15 s) para absorver atrasos transitórios.

**Detecção lazy para atuadores** — atuadores ficam passivamente aguardando TCP. Monitoramento periódico geraria tráfego desnecessário. A falha só importa quando um comando é enviado; `ConnectionRefusedError` / `TimeoutError` disparam a remoção imediata do `active_devices`.

**Discovery `while True` a cada 5 s** — garante que dispositivos ligados após o Gateway, dispositivos que reiniciaram e pacotes descartados por rede instável sempre terão uma nova janela para se registrar. Torna o sistema **auto-cicatrizante** sem intervenção manual.

---

## Tolerância a Falhas

| Cenário | Comportamento |
|---|---|
| Sensor desligado (Ctrl+C) | Nenhum pacote de desconexão é enviado. Em ≤35 s o Fault-Monitor remove o dispositivo. Histórico UDP fica em `sensor_history`. |
| Gateway reiniciado | Sensores/atuadores continuam rodando (UDP é fire-and-forget; TCP aguarda). `SO_REUSEADDR` evita `Address already in use`. Em ≤5 s o próximo broadcast repopula `active_devices`. |
| Cliente durante queda do Gateway | `settimeout(5)` + `except ConnectionRefusedError/timeout` garantem que o cliente nunca trava; retorna ao menu com mensagem de erro. |
| Comando a atuador offline | Gateway recebe `ConnectionRefusedError`; remove o atuador lazily; retorna `ERROR` ao cliente. |

**Limitação:** `sensor_history` é em memória — reiniciar o Gateway apaga o histórico de leituras.

---

## Execução

```bash
# 1. Gateway (sempre primeiro)
python gateway.py

# 2. Dispositivos (qualquer ordem, em terminais separados)
# Exemplo — crie arquivos mínimos de execução:
python -c "from dispositivos import Continuos; Continuos('TEMPERATURE_SENSOR','Celsius').iniciar()"
python -c "from dispositivos import SensorControlavel; SensorControlavel('AIR_QUALITY_SENSOR','µg/m³').iniciar()"
python -c "from dispositivos import Atuador; Atuador('LAMP_POST').iniciar()"

# 3. Cliente (por último)
python cliente.py
```

**Comandos do cliente:**
- `[1]` LIST\_DEVICES — lista todos os dispositivos online
- `[2]` GET\_AVG `<device_id>` — média de todas as leituras do sensor
- `[3]` SET\_STATE `<device_id> 1|0` — liga ou desliga atuador

**Simular falhas:**
- Derrubar sensor → aguardar 35 s → ver alerta no Gateway
- Reiniciar Gateway → dispositivos se re-registram em ≤ 5 s automaticamente
- Derrubar atuador → tentar SET\_STATE → Gateway remove e informa o cliente

---

> Documentação completa com diagramas, trechos de código e análise aprofundada em [DOCUMENTACAO.md](DOCUMENTACAO.md).

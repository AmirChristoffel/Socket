# Smart City IoT — Gateway Inteligente (SD)

Sistema IoT distribuído para uma **Cidade Inteligente**, desenvolvido na disciplina de Sistemas Distribuídos. Dispositivos heterogêneos (sensores, atuadores) se registram automaticamente em um Gateway centralizado sem configuração manual de endereços. Um **dashboard web** permite monitorar e controlar tudo em tempo real.

---

## Por que este sistema existe

O projeto demonstra na prática os conceitos centrais de SD:

- **Descoberta automática** via UDP Multicast — nenhum endereço precisa ser configurado a priori
- **Telemetria tolerante à perda** via UDP — dados de sensor não precisam de entrega garantida
- **Controle confiável** via TCP — comandos de ligar/desligar exigem confirmação
- **Interoperabilidade de linguagens** via Protocol Buffers — o sensor em Rust fala o mesmo protocolo que os sensores Python
- **Detecção de falhas** — sensores removidos por timeout de 25 s, atuadores por prova TCP em ≤ 10 s

---

## Arquitetura em uma linha

```
Dispositivos ──UDP 5008──► Gateway ──TCP 5009──► Cliente/API
Gateway      ──UDP Multicast 5007──► Dispositivos (descoberta)
API (FastAPI :8000) ──SSE──► Dashboard (Next.js :3000)
```

| Camada | Arquivo | Papel |
|---|---|---|
| Gateway | `gateway.py` | Descobre dispositivos, armazena leituras, atende comandos TCP |
| Dispositivos Python | `dispositivos.py` | Classes `Continuos`, `Atuador`, `SensorControlavel` |
| Sensor Rust | `dispositivo_rust/` | Sensor de temperatura em Rust — mesma interface Protobuf |
| Cliente CLI | `cliente.py` | Terminal interativo para consultar e comandar |
| Bridge REST/SSE | `api.py` | FastAPI: gerencia subprocessos + proxy para o Gateway |
| Dashboard | `dashboard/` | Next.js 14 — visualização e controle via navegador |

---

## Pré-requisitos

| Ferramenta | Versão | Instalação |
|---|---|---|
| Python | 3.11+ | python.org |
| pip packages | — | `pip install protobuf fastapi "uvicorn[standard]"` |
| Node.js | 18+ | nodejs.org |
| Rust / Cargo | stable | `winget install Rustlang.Rustup` (só para o sensor Rust) |

---

## Como rodar

### Opção A — Dashboard (recomendado)

O dashboard gerencia todos os processos internamente via `api.py`.

```bash
# 1. Instalar dependências Python
pip install protobuf fastapi "uvicorn[standard]"

# 2. Terminal A — API bridge
python api.py

# 3. Terminal B — Dashboard
cd dashboard
npm install   # apenas na primeira vez
npm run dev
```

Acesse `http://localhost:3000`. Clique em **Iniciar** nos slots para ligar Gateway, sensores e atuadores. O botão **Conectar Cliente** no painel "Terminal do Cliente" ativa o stream de eventos em tempo real.

---

### Opção B — Linha de comando (sem dashboard)

```bash
# Terminal 1 — Gateway (sempre primeiro)
python gateway.py

# Terminal 2 — Sensor de temperatura Python
python -c "from dispositivos import Continuos; Continuos('TEMPERATURE_SENSOR','Celsius').iniciar()"

# Terminal 3 — Sensor de qualidade do ar (controlável)
python -c "from dispositivos import SensorControlavel; SensorControlavel('AIR_QUALITY_SENSOR','ug/m3').iniciar()"

# Terminal 4 — Atuador (poste)
python -c "from dispositivos import Atuador; Atuador('LAMP_POST').iniciar()"

# Terminal 5 — Sensor Rust (opcional)
cd dispositivo_rust && cargo run

# Terminal 6 — Cliente analítico
python cliente.py
```

**Comandos do cliente CLI:**

| Opção | O que faz |
|---|---|
| `1` LIST_DEVICES | Lista todos os dispositivos online com tipo, IP e estado |
| `2` GET_AVG | Média de todas as leituras de um sensor específico |
| `3` SET_STATE | Liga (`1`) ou desliga (`0`) um atuador |

---

## O que acontece quando você roda

1. Gateway inicia e começa broadcasts multicast a cada 5 s na porta 5007
2. Cada dispositivo recebe o broadcast, descobre o IP do Gateway e envia um anúncio UDP para a porta 5008
3. Gateway registra o dispositivo em memória
4. Sensores enviam leituras UDP a cada 15 s; Gateway armazena em `sensor_history`
5. Dashboard consulta e controla via TCP na porta 5009 (através da API na porta 8000)
6. Se um sensor parar, é removido em ≤ 25 s; se um atuador parar, em ≤ 10 s
7. Se o Gateway reiniciar, os dispositivos se re-registram em ≤ 5 s automaticamente

---

## Limitação conhecida

`sensor_history` é mantido apenas em memória — reiniciar o Gateway apaga o histórico de leituras.

> Documentação técnica completa (protocolos, decisões de design, fluxos de dados, referência da API): [DOCUMENTACAO.md](DOCUMENTACAO.md)

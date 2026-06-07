# api.py — Bridge REST/SSE entre o dashboard Next.js e o sistema IoT
#
# Instalar dependências:
#   pip install fastapi uvicorn[standard]
#
# Executar:
#   python api.py

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from protos import todolist_pb2

# ── Setup ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Smart City IoT Bridge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR    = Path(__file__).parent
GATEWAY_IP  = "127.0.0.1"
GATEWAY_TCP = 5009
LOGS_DIR    = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Caminho explícito do cargo no Windows (rustup instala em ~/.cargo/bin)
_CARGO = str(Path.home() / ".cargo" / "bin" / "cargo.exe")

# ── Definição dos slots de dispositivos ────────────────────────────────────────
#
# Cada slot mapeia um nome fixo a um comando de processo.
# O frontend liga/desliga cada slot independentemente.

SLOTS: dict[str, dict] = {
    "gateway": {
        "label": "Gateway Inteligente",
        "cmd":   [sys.executable, "gateway.py"],
        "cwd":   str(BASE_DIR),
    },
    "sensor_temperatura_py": {
        "label": "Sensor de Temperatura (Python)",
        "cmd":   [sys.executable, "-c",
                  "from dispositivos import Continuos;"
                  " Continuos('TEMPERATURE_SENSOR','Celsius').iniciar()"],
        "cwd":   str(BASE_DIR),
    },
    "sensor_ar": {
        "label": "Sensor de Qualidade do Ar",
        "cmd":   [sys.executable, "-c",
                  "from dispositivos import SensorControlavel;"
                  " SensorControlavel('AIR_QUALITY_SENSOR','ug/m3').iniciar()"],
        "cwd":   str(BASE_DIR),
    },
    "poste": {
        "label": "Poste de Luz",
        "cmd":   [sys.executable, "-c",
                  "from dispositivos import Atuador; Atuador('LAMP_POST').iniciar()"],
        "cwd":   str(BASE_DIR),
    },
    "semaforo": {
        "label": "Semáforo",
        "cmd":   [sys.executable, "-c",
                  "from dispositivos import Atuador; Atuador('TRAFFIC_LIGHT').iniciar()"],
        "cwd":   str(BASE_DIR),
    },
    "camera": {
        "label": "Câmera",
        "cmd":   [sys.executable, "-c",
                  "from dispositivos import Atuador; Atuador('CAMERA').iniciar()"],
        "cwd":   str(BASE_DIR),
    },
    "sensor_rust": {
        "label": "Sensor de Temperatura (Rust)",
        "cmd":   [_CARGO, "run"],
        "cwd":   str(BASE_DIR / "dispositivo_rust"),
    },
}

# Registro em memória: slot → Popen
_procs: dict[str, subprocess.Popen] = {}

# ── Helpers de processo ────────────────────────────────────────────────────────

def _running(slot: str) -> bool:
    p = _procs.get(slot)
    return p is not None and p.poll() is None   # poll() == None → ainda vivo


def _all_statuses() -> dict:
    return {
        slot: {"label": info["label"], "running": _running(slot)}
        for slot, info in SLOTS.items()
    }

# ── Helpers de comunicação TCP com o Gateway ───────────────────────────────────

def _tcp(req: todolist_pb2.ClientRequest) -> Optional[str]:
    """Envia um ClientRequest Protobuf via TCP e retorna a string de resposta."""
    try:
        envelope = todolist_pb2.SmartCityMessage(client_request=req)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect((GATEWAY_IP, GATEWAY_TCP))
            s.sendall(envelope.SerializeToString())
            raw = s.recv(4096)
        if not raw:
            return None
        msg = todolist_pb2.SmartCityMessage()
        msg.ParseFromString(raw)
        return msg.gateway_response.message
    except Exception:
        return None


def _parse_readings(raw: str) -> list[dict]:
    """Converte a string de GET_HISTORY em lista de dicts estruturados."""
    readings = []
    for line in raw.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        reading: dict = {}
        for part in line.split("|"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                reading[k.strip()] = v.strip()
        if reading:
            readings.append(reading)
    return readings


def _parse_devices(raw: str) -> list[dict]:
    """Converte a string de LIST_DEVICES em lista de dicts estruturados.

    Formato esperado por linha:
      [1] device_id | tipo=TEMPERATURE_SENSOR | atuador=False | ip=127.0.0.1:PORT
    """
    devices = []
    for line in raw.splitlines():
        line = line.strip()
        if "|" not in line or not line.startswith("["):
            continue
        parts = line.split("|")
        device_id = parts[0].split("]", 1)[-1].strip()
        device: dict = {"device_id": device_id}
        for part in parts[1:]:
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                device[k.strip()] = v.strip()
        devices.append(device)
    return devices

# ── Endpoints — Gestão de Processos ───────────────────────────────────────────

@app.get("/processes",
         summary="Lista todos os slots e se estão rodando")
def get_processes():
    return _all_statuses()


@app.post("/processes/{slot}/start",
          summary="Inicia o processo de um slot")
def start_process(slot: str):
    if slot not in SLOTS:
        raise HTTPException(404, f"Slot '{slot}' não existe.")
    if _running(slot):
        return {"ok": False, "detail": "Já está rodando."}
    info = SLOTS[slot]
    try:
        log_file = open(LOGS_DIR / f"{slot}.log", "w", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"   # flush imediato nos logs Python
        # Dispositivos iniciados via API pulam a descoberta multicast (porta 5007
        # pode ser bloqueada no Windows com múltiplos processos) e conectam direto.
        if slot != "gateway":
            env["GATEWAY_ADDR"] = "127.0.0.1:5008"
        p = subprocess.Popen(
            info["cmd"],
            cwd=info["cwd"],
            stdout=log_file,
            stderr=log_file,
            env=env,
        )
        _procs[slot] = p
        return {"ok": True, "pid": p.pid, "slot": slot, "label": info["label"]}
    except FileNotFoundError as e:
        raise HTTPException(500, f"Executável não encontrado: {e}")


@app.get("/processes/{slot}/logs",
         summary="Últimas linhas do log de stdout/stderr do processo")
def process_logs(slot: str, lines: int = 40):
    if slot not in SLOTS:
        raise HTTPException(404, f"Slot '{slot}' não existe.")
    log_path = LOGS_DIR / f"{slot}.log"
    if not log_path.exists():
        return {"lines": [], "detail": "Sem log ainda (processo nunca iniciado)."}
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": content[-lines:]}


@app.post("/processes/{slot}/stop",
          summary="Para o processo de um slot")
def stop_process(slot: str):
    if slot not in SLOTS:
        raise HTTPException(404, f"Slot '{slot}' não existe.")
    p = _procs.get(slot)
    if p is None or p.poll() is not None:
        return {"ok": False, "detail": "Não está rodando."}
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
    _procs.pop(slot, None)
    return {"ok": True, "slot": slot}

# ── Endpoints — Proxy para o Gateway ──────────────────────────────────────────

@app.get("/gateway/ping",
         summary="Verifica se o Gateway está acessível")
def ping_gateway():
    online = _tcp(todolist_pb2.ClientRequest(command="PING")) == "PONG"
    return {"online": online}


@app.get("/gateway/devices",
         summary="Lista dispositivos conectados (LIST_DEVICES)")
def list_devices():
    raw = _tcp(todolist_pb2.ClientRequest(command="LIST_DEVICES"))
    if raw is None:
        raise HTTPException(503, "Gateway inacessível.")
    return {"devices": _parse_devices(raw), "count": len(_parse_devices(raw))}


@app.get("/gateway/avg/{device_id}",
         summary="Média de leituras de um sensor (GET_AVG)")
def get_avg(device_id: str):
    raw = _tcp(todolist_pb2.ClientRequest(
        command="GET_AVG",
        target_device_id=device_id,
    ))
    if raw is None:
        raise HTTPException(503, "Gateway inacessível.")
    return {"message": raw}


@app.get("/gateway/readings",
         summary="Últimas leituras de sensores (GET_HISTORY)")
def get_readings():
    raw = _tcp(todolist_pb2.ClientRequest(command="GET_HISTORY"))
    if raw is None:
        raise HTTPException(503, "Gateway inacessível.")
    return {"readings": _parse_readings(raw)}


class CommandPayload(BaseModel):
    device_id: str
    state: bool


@app.post("/gateway/command",
          summary="Liga ou desliga um atuador (SET_STATE)")
def send_command(payload: CommandPayload):
    raw = _tcp(todolist_pb2.ClientRequest(
        command="SET_STATE",
        target_device_id=payload.device_id,
        new_state=payload.state,
    ))
    if raw is None:
        raise HTTPException(503, "Gateway inacessível.")
    return {"message": raw}

# ── SSE — Stream de eventos em tempo real ─────────────────────────────────────
#
# O frontend se conecta em GET /events e recebe eventos a cada 3 segundos:
#   event: processes  → status de cada slot (running true/false)
#   event: gateway    → se o gateway está online
#   event: devices    → lista de dispositivos conectados ao gateway

async def _event_stream():
    while True:
        # 1. Status dos processos locais (nunca falha)
        yield f"event: processes\ndata: {json.dumps(_all_statuses())}\n\n"

        # 2. Gateway online?
        gw_online = await asyncio.to_thread(
            _tcp, todolist_pb2.ClientRequest(command="PING")
        ) == "PONG"
        yield f"event: gateway\ndata: {json.dumps({'online': gw_online})}\n\n"

        # 3. Dispositivos e leituras (só se gateway está online)
        if gw_online:
            raw = await asyncio.to_thread(
                _tcp, todolist_pb2.ClientRequest(command="LIST_DEVICES")
            )
            if raw:
                devices = _parse_devices(raw)
                yield f"event: devices\ndata: {json.dumps({'devices': devices})}\n\n"

            raw_hist = await asyncio.to_thread(
                _tcp, todolist_pb2.ClientRequest(command="GET_HISTORY")
            )
            if raw_hist:
                readings = _parse_readings(raw_hist)
                yield f"event: readings\ndata: {json.dumps({'readings': readings})}\n\n"

        await asyncio.sleep(3)


@app.get("/events",
         summary="SSE — stream de eventos do sistema em tempo real")
async def sse_stream():
    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

"use client";

import { useRef, useState } from "react";
import { useSSE } from "@/hooks/useSSE";
import { startProcess, stopProcess, sendCommand, getAvg, pingGateway, listDevices } from "@/lib/api";
import type { Device, Reading } from "@/lib/api";

// ── Configuração dos slots ──────────────────────────────────────────────────
const SLOTS = [
  { key: "gateway",             label: "Gateway",           color: "blue"   },
  { key: "sensor_temperatura_py", label: "Temp. Python",    color: "orange" },
  { key: "sensor_ar",           label: "Qualidade do Ar",   color: "teal"   },
  { key: "sensor_rust",         label: "Temp. Rust",        color: "red"    },
  { key: "poste",               label: "Poste de Luz",      color: "yellow" },
  { key: "semaforo",            label: "Semáforo",          color: "green"  },
  { key: "camera",              label: "Câmera",            color: "purple" },
];

const COLOR_MAP: Record<string, string> = {
  blue:   "border-blue-500",
  orange: "border-orange-500",
  teal:   "border-teal-500",
  red:    "border-red-500",
  yellow: "border-yellow-500",
  green:  "border-green-500",
  purple: "border-purple-500",
};

// ── Componente: Card de processo ───────────────────────────────────────────
function ProcessCard({
  slotKey, label, color, running, onToggle,
}: {
  slotKey: string; label: string; color: string;
  running: boolean; onToggle: (slot: string, run: boolean) => void;
}) {
  return (
    <div className={`bg-gray-900 border-t-2 ${COLOR_MAP[color]} rounded-lg p-4 flex flex-col gap-3`}>
      <div className="flex items-center justify-between">
        <span className="font-semibold text-sm text-gray-200">{label}</span>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
          running ? "bg-green-900 text-green-300" : "bg-gray-800 text-gray-500"
        }`}>
          {running ? "Rodando" : "Parado"}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <div className={`w-2 h-2 rounded-full ${running ? "bg-green-400" : "bg-gray-600"}`} />
        <button
          onClick={() => onToggle(slotKey, !running)}
          className={`flex-1 py-1.5 rounded text-xs font-semibold transition-colors ${
            running
              ? "bg-red-700 hover:bg-red-600 text-white"
              : "bg-green-700 hover:bg-green-600 text-white"
          }`}
        >
          {running ? "Desligar" : "Ligar"}
        </button>
      </div>
    </div>
  );
}

// ── Componente: Linha de dispositivo ────────────────────────────────────────
function DeviceRow({
  device, onCommand, onAvg,
}: {
  device: { device_id: string; tipo?: string; atuador?: string; ip?: string; estado?: string };
  onCommand: (id: string, state: boolean) => void;
  onAvg: (id: string) => void;
}) {
  const isActuator = device.atuador === "True";
  const isOn = device.estado === "True";
  return (
    <tr className="border-b border-gray-800 hover:bg-gray-800/40 transition-colors">
      <td className="py-2 px-3 font-mono text-xs text-gray-300 max-w-[160px] truncate">{device.device_id}</td>
      <td className="py-2 px-3 text-xs text-gray-400">{device.tipo ?? "—"}</td>
      <td className="py-2 px-3">
        <span className={`text-xs px-1.5 py-0.5 rounded ${
          isActuator ? "bg-blue-900 text-blue-300" : "bg-teal-900 text-teal-300"
        }`}>
          {isActuator ? "Atuador" : "Sensor"}
        </span>
      </td>
      <td className="py-2 px-3">
        {isActuator ? (
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
            isOn ? "bg-green-900 text-green-300" : "bg-gray-800 text-gray-500"
          }`}>
            {isOn ? "Ligado" : "Desligado"}
          </span>
        ) : (
          <span className="text-xs px-1.5 py-0.5 rounded font-medium bg-teal-900 text-teal-300">
            Ativo
          </span>
        )}
      </td>
      <td className="py-2 px-3 text-xs text-gray-500">{device.ip ?? "—"}</td>
      <td className="py-2 px-3">
        <div className="flex gap-1.5">
          {isActuator ? (
            <>
              <button onClick={() => onCommand(device.device_id, true)}
                className="px-2 py-1 bg-green-800 hover:bg-green-700 text-green-200 text-xs rounded">
                Ligar
              </button>
              <button onClick={() => onCommand(device.device_id, false)}
                className="px-2 py-1 bg-red-800 hover:bg-red-700 text-red-200 text-xs rounded">
                Desligar
              </button>
            </>
          ) : (
            <button onClick={() => onAvg(device.device_id)}
              className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-200 text-xs rounded">
              Ver média
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

// ── Componente: Terminal do Cliente Analítico ───────────────────────────────
type TermLine = { dir: "cmd" | "resp" | "err"; text: string; time: string };

function ClientTerminal({
  devices, connected, onConnect, onDisconnect,
}: {
  devices: Device[];
  connected: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const [lines, setLines] = useState<TermLine[]>([]);
  const [busy, setBusy] = useState(false);
  const [selectedSensor, setSelectedSensor] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const sensors = devices.filter((d) => d.atuador !== "True");

  function write(dir: TermLine["dir"], text: string) {
    const time = new Date().toLocaleTimeString("pt-BR");
    setLines((prev) => [...prev, { dir, text, time }].slice(-100));
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }

  async function handlePing() {
    setBusy(true);
    write("cmd", "PING → 127.0.0.1:5009");
    try {
      const r = await pingGateway();
      write(
        r.online ? "resp" : "err",
        r.online ? "PONG — Gateway conectado" : "Sem resposta — Gateway offline",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleList() {
    setBusy(true);
    write("cmd", "LIST_DEVICES");
    try {
      const r = await listDevices();
      write("resp", `${r.count} dispositivo(s) conectado(s):`);
      r.devices.forEach((d, i) => {
        write("resp", `  [${i + 1}] ${d.device_id} | tipo=${d.tipo ?? "?"} | atuador=${d.atuador} | ip=${d.ip ?? "?"}`);
      });
    } catch {
      write("err", "Erro ao listar dispositivos.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAvg() {
    if (!selectedSensor) return;
    setBusy(true);
    write("cmd", `GET_AVG ${selectedSensor}`);
    try {
      const r = await getAvg(selectedSensor);
      write("resp", r.message);
    } catch {
      write("err", "Erro ao consultar média.");
    } finally {
      setBusy(false);
    }
  }

  function handleClientToggle() {
    if (connected) {
      write("err", "Cliente desconectado do Gateway");
      onDisconnect();
    } else {
      write("cmd", "Reconectando ao Gateway...");
      onConnect();
      write("resp", "Cliente conectado");
    }
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 flex flex-col lg:flex-row overflow-hidden">
      {/* Saída do terminal */}
      <div className="flex-1 bg-gray-950 p-4 font-mono text-xs overflow-y-auto h-52 lg:h-64">
        {lines.length === 0 ? (
          <p className="text-gray-600">Terminal aguardando comandos...</p>
        ) : (
          lines.map((l, i) => (
            <div key={i} className="flex gap-2 leading-5">
              <span className="text-gray-600 shrink-0">{l.time}</span>
              <span className={
                l.dir === "cmd" ? "text-cyan-400 shrink-0" :
                l.dir === "err" ? "text-red-400 shrink-0" :
                "text-green-400 shrink-0"
              }>
                {l.dir === "cmd" ? "→" : l.dir === "err" ? "✗" : "←"}
              </span>
              <span className={
                l.dir === "cmd" ? "text-cyan-300 whitespace-pre" :
                l.dir === "err" ? "text-red-300" :
                "text-gray-300 whitespace-pre"
              }>
                {l.text}
              </span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* Painel de comandos */}
      <div className="w-full lg:w-60 border-t lg:border-t-0 lg:border-l border-gray-800 p-4 flex flex-col gap-3">
        {/* Status do cliente */}
        <div className="flex items-center justify-between">
          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest">
            Cliente — TCP :5009
          </p>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
            connected ? "bg-green-900 text-green-300" : "bg-red-900 text-red-400"
          }`}>
            {connected ? "Conectado" : "Desconectado"}
          </span>
        </div>

        <button
          onClick={handleClientToggle}
          className={`w-full py-2 text-white text-xs rounded font-semibold transition-colors ${
            connected
              ? "bg-red-800 hover:bg-red-700"
              : "bg-green-800 hover:bg-green-700"
          }`}
        >
          {connected ? "Desligar Cliente" : "Conectar Cliente"}
        </button>

        <div className="border-t border-gray-800 pt-3 flex flex-col gap-2">
          <button
            onClick={handlePing}
            disabled={busy || !connected}
            className="w-full py-2 bg-blue-800 hover:bg-blue-700 disabled:opacity-40 text-white text-xs rounded font-semibold transition-colors"
          >
            Conectar (PING)
          </button>

          <button
            onClick={handleList}
            disabled={busy || !connected}
            className="w-full py-2 bg-teal-800 hover:bg-teal-700 disabled:opacity-40 text-white text-xs rounded font-semibold transition-colors"
          >
            Listar Dispositivos
          </button>
        </div>

        <div className="border-t border-gray-800 pt-3 flex flex-col gap-2">
          <p className="text-[10px] text-gray-500 uppercase tracking-wider">Consultar Média</p>
          <select
            value={selectedSensor}
            onChange={(e) => setSelectedSensor(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 text-gray-300 text-xs rounded px-2 py-1.5"
          >
            <option value="">— selecionar sensor —</option>
            {sensors.map((d) => (
              <option key={d.device_id} value={d.device_id}>
                {d.device_id}
              </option>
            ))}
          </select>
          <button
            onClick={handleAvg}
            disabled={busy || !selectedSensor || !connected}
            className="w-full py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-gray-200 text-xs rounded font-semibold transition-colors"
          >
            GET_AVG
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Componente: Leituras ao Vivo ────────────────────────────────────────────
function LiveReadings({ readings }: { readings: Reading[] }) {
  const reversed = [...readings].reverse();
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 flex flex-col">
      <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-200">Leituras ao Vivo</h2>
        <span className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse" />
          <span className="text-xs text-gray-500">sensor data</span>
        </span>
      </div>
      <div className="flex-1 overflow-y-auto max-h-64 divide-y divide-gray-800">
        {reversed.length === 0 ? (
          <p className="py-6 text-center text-gray-600 text-xs">
            Aguardando leituras de sensores...
          </p>
        ) : (
          reversed.map((r, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-2 hover:bg-gray-800/40">
              <span className="font-mono text-lg font-bold text-teal-400 w-20 shrink-0 text-right">
                {parseFloat(r.value).toFixed(1)}
              </span>
              <span className="text-xs text-gray-500 w-14 shrink-0">{r.unit}</span>
              <span className="text-xs text-gray-400 truncate flex-1 font-mono">{r.device_id}</span>
              <span className="text-[10px] text-gray-600 shrink-0 font-mono">
                {new Date(parseInt(r.ts) * 1000).toLocaleTimeString("pt-BR")}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

const SENSOR_SLOTS = ["sensor_temperatura_py", "sensor_ar", "sensor_rust"];

// ── Página principal ────────────────────────────────────────────────────────
export default function Dashboard() {
  const { processes, gatewayOnline, devices, readings, log, connected, connect, disconnect } = useSSE();
  const [toast, setToast] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  }

  async function handleToggle(slot: string, shouldStart: boolean) {
    setLoading(slot);
    try {
      const res = shouldStart ? await startProcess(slot) : await stopProcess(slot);
      showToast(res.detail ?? (shouldStart ? `${slot} iniciado.` : `${slot} parado.`));
    } finally {
      setLoading(null);
    }
  }

  async function handleStopAllSensors() {
    for (const slot of SENSOR_SLOTS) {
      if (processes[slot]?.running) await stopProcess(slot);
    }
    showToast("Todos os sensores desligados.");
  }

  async function handleCommand(deviceId: string, state: boolean) {
    try {
      const res = await sendCommand(deviceId, state);
      showToast(res.message);
    } catch {
      showToast("Erro ao enviar comando.");
    }
  }

  async function handleAvg(deviceId: string) {
    try {
      const res = await getAvg(deviceId);
      showToast(res.message);
    } catch {
      showToast("Erro ao consultar média.");
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 p-6 font-sans">

      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-50 bg-gray-800 border border-gray-700 text-gray-100 text-sm px-4 py-3 rounded-lg shadow-xl max-w-sm">
          {toast}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Smart City IoT</h1>
          <p className="text-gray-400 text-sm">Painel de controle distribuído</p>
        </div>
        <div className="flex items-center gap-2 bg-gray-900 border border-gray-800 rounded-lg px-4 py-2">
          <div className={`w-2.5 h-2.5 rounded-full ${gatewayOnline ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
          <span className="text-sm font-medium text-gray-300">
            Gateway {gatewayOnline ? "Online" : "Offline"}
          </span>
        </div>
      </div>

      {/* Controle de Processos */}
      <section className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-widest">
            Controle de Processos
          </h2>
          <button
            onClick={handleStopAllSensors}
            className="text-xs px-3 py-1 bg-red-900 hover:bg-red-800 text-red-300 rounded font-medium transition-colors"
          >
            Desligar todos os sensores
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7 gap-3">
          {SLOTS.map(({ key, label, color }) => {
            const proc = processes[key];
            return (
              <div key={key} className={loading === key ? "opacity-60 pointer-events-none" : ""}>
                <ProcessCard
                  slotKey={key}
                  label={label}
                  color={color}
                  running={proc?.running ?? false}
                  onToggle={handleToggle}
                />
              </div>
            );
          })}
        </div>
      </section>

      {/* Terminal do Cliente Analítico */}
      <section className="mb-4">
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">
          Terminal do Cliente
        </h2>
        <ClientTerminal
          devices={devices}
          connected={connected}
          onConnect={connect}
          onDisconnect={disconnect}
        />
      </section>

      {/* Linha inferior: dispositivos + coluna direita */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Dispositivos conectados */}
        <div className="lg:col-span-2 bg-gray-900 rounded-lg border border-gray-800">
          <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-200">Dispositivos Conectados</h2>
            <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full">
              {devices.length} online
            </span>
          </div>
          <div className="overflow-x-auto">
            {devices.length === 0 ? (
              <div className="py-8 text-center text-gray-600 text-sm">
                {gatewayOnline ? "Nenhum dispositivo conectado." : "Gateway offline."}
              </div>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
                    <th className="py-2 px-3 font-medium">ID</th>
                    <th className="py-2 px-3 font-medium">Tipo</th>
                    <th className="py-2 px-3 font-medium">Papel</th>
                    <th className="py-2 px-3 font-medium">Estado</th>
                    <th className="py-2 px-3 font-medium">Endereço</th>
                    <th className="py-2 px-3 font-medium">Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => (
                    <DeviceRow
                      key={d.device_id}
                      device={d}
                      onCommand={handleCommand}
                      onAvg={handleAvg}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Coluna direita: leituras ao vivo + log de eventos */}
        <div className="flex flex-col gap-4">
          <LiveReadings readings={readings} />

          <div className="bg-gray-900 rounded-lg border border-gray-800 flex flex-col">
            <div className="px-4 py-3 border-b border-gray-800">
              <h2 className="text-sm font-semibold text-gray-200">Log de Eventos</h2>
              <p className="text-xs text-gray-500">Atualizações em tempo real (SSE)</p>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-1 max-h-48">
              {log.length === 0 ? (
                <p className="text-gray-600 text-xs">Aguardando eventos...</p>
              ) : (
                log.map((entry, i) => (
                  <div key={i} className="flex gap-2 items-start text-xs">
                    <span className="text-gray-600 font-mono shrink-0">{entry.time}</span>
                    <span className={`shrink-0 px-1.5 rounded text-[10px] font-medium ${
                      entry.event === "gateway"   ? "bg-blue-900 text-blue-300"
                      : entry.event === "devices"   ? "bg-teal-900 text-teal-300"
                      : entry.event === "discovery" ? "bg-green-900 text-green-300"
                      : entry.event === "desconexão"? "bg-orange-900 text-orange-300"
                      : entry.event === "cliente"   ? "bg-purple-900 text-purple-300"
                      : entry.event === "erro"      ? "bg-red-900 text-red-300"
                      : "bg-gray-800 text-gray-400"
                    }`}>
                      {entry.event}
                    </span>
                    <span className="text-gray-400 truncate">{entry.summary}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Device, ProcessStatus, Reading } from "@/lib/api";

export type LogEntry = { time: string; event: string; summary: string };

export function useSSE() {
  const [processes, setProcesses] = useState<Record<string, ProcessStatus>>({});
  const [gatewayOnline, setGatewayOnline] = useState(false);
  const [devices, setDevices] = useState<Device[]>([]);
  const [readings, setReadings] = useState<Reading[]>([]);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);

  const esRef = useRef<EventSource | null>(null);
  const knownIdsRef = useRef<Set<string>>(new Set());
  const prevCountRef = useRef<number>(-1);
  const prevRunningRef = useRef<number>(-1);
  const prevGwRef = useRef<boolean | null>(null);

  const addLog = useCallback((event: string, summary: string) => {
    const time = new Date().toLocaleTimeString("pt-BR");
    setLog((prev) => [{ time, event, summary }, ...prev].slice(0, 50));
  }, []);

  const connect = useCallback(() => {
    if (esRef.current) esRef.current.close();

    const es = new EventSource("http://localhost:8000/events");
    esRef.current = es;

    es.addEventListener("processes", (e) => {
      const data = JSON.parse(e.data) as Record<string, ProcessStatus>;
      setProcesses(data);
      const running = Object.values(data).filter((p) => p.running).length;
      if (running !== prevRunningRef.current) {
        addLog("processes", `${running} processo(s) rodando`);
        prevRunningRef.current = running;
      }
    });

    es.addEventListener("gateway", (e) => {
      const data = JSON.parse(e.data) as { online: boolean };
      setGatewayOnline(data.online);
      if (data.online !== prevGwRef.current) {
        addLog("gateway", data.online ? "Online" : "Offline");
        prevGwRef.current = data.online;
      }
    });

    es.addEventListener("devices", (e) => {
      const data = JSON.parse(e.data) as { devices: Device[] };
      const incoming = data.devices;
      const incomingIds = new Set(incoming.map((d) => d.device_id));

      for (const d of incoming) {
        if (!knownIdsRef.current.has(d.device_id)) {
          addLog("discovery", `${d.device_id} conectado`);
        }
      }
      for (const id of knownIdsRef.current) {
        if (!incomingIds.has(id)) {
          addLog("desconexão", `${id} removido`);
        }
      }

      knownIdsRef.current = incomingIds;
      setDevices(incoming);

      if (incoming.length !== prevCountRef.current) {
        addLog("devices", `${incoming.length} dispositivo(s) conectado(s)`);
        prevCountRef.current = incoming.length;
      }
    });

    es.addEventListener("readings", (e) => {
      const data = JSON.parse(e.data) as { readings: Reading[] };
      setReadings(data.readings);
    });

    es.onopen = () => setConnected(true);

    es.onerror = () => {
      addLog("erro", "Conexão com a API perdida");
      setConnected(false);
    };
  }, [addLog]);

  const disconnect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setConnected(false);
    addLog("cliente", "Cliente desconectado do Gateway");
  }, [addLog]);

  useEffect(() => {
    connect();
    return () => esRef.current?.close();
  }, [connect]);

  return { processes, gatewayOnline, devices, readings, log, connected, connect, disconnect };
}

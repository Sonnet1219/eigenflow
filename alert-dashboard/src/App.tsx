import { useEffect, useMemo, useState } from "react";
import "./App.css";

type SSECallback = (event: string, payload: unknown) => void;

async function consumeSSE(
  response: Response,
  onEvent: SSECallback
): Promise<void> {
  if (!response.body) {
    throw new Error("SSE response has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const processBlock = (block: string) => {
    if (!block.trim()) return;
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim() || "message";
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }
    const rawData = dataLines.join("\n");
    let payload: unknown = rawData;
    if (!rawData.length) {
      payload = {};
    } else {
      try {
        payload = JSON.parse(rawData);
      } catch {
        payload = rawData;
      }
    }
    onEvent(eventName, payload);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      processBlock(block);
    }
  }

  if (buffer.length) {
    processBlock(buffer);
  }
}

type Severity = "warn" | "critical" | "emergency";

type AlertRow = {
  lp: string;
  severity: Severity;
  marginLevel: string;
  status: string;
  traceId: string;
  threadId: string;
  firstTriggeredAt: string;
  lastUpdatedAt: string;
  ignoreUntil: string;
  latestReport?: string;
};

type MonitoringStatus = {
  status: "running" | "stopped";
  thresholds: Record<Severity, { trigger: number; clear: number }>;
  interval: number;
  blowupMarginLevel: number;
  alerts: Record<string, AlertRow>;
};

type HumanAction = "recheck" | "ignore" | "comment";

const API_ROOT = import.meta.env.VITE_ALERT_API_ROOT || "http://0.0.0.0:8002";
const STATUS_POLL_INTERVAL = 30_000;
const LOG_LIMIT = 25;

const STATUS_LABELS: Record<string, string> = {
  active: "active",
  awaiting_hitl: "awaiting HITL",
  recheck_pending: "rechecked – still high",
  ignored: "ignored",
  resolved: "resolved",
  auto_cleared: "auto-cleared",
};

function stringifyError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return typeof err === "string" ? err : JSON.stringify(err);
}

function App() {
  const [data, setData] = useState<MonitoringStatus | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [ignoreMinutes, setIgnoreMinutes] = useState<number>(60);
  const [loading, setLoading] = useState<boolean>(false);
  const [controlLoading, setControlLoading] = useState<boolean>(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [streamingTrace, setStreamingTrace] = useState<string | null>(null);
  const [streamingReport, setStreamingReport] = useState<string>("");
  const [historyContent, setHistoryContent] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState<boolean>(false);

  const alerts = useMemo(() => {
    if (!data) return [] as AlertRow[];
    return Object.values(data.alerts).sort((a, b) =>
      b.lastUpdatedAt.localeCompare(a.lastUpdatedAt)
    );
  }, [data]);

  const selectedAlert = useMemo(
    () => alerts.find((alert) => alert.traceId === selectedTrace),
    [alerts, selectedTrace]
  );

  useEffect(() => {
    setHistoryContent(null);
  }, [selectedTrace]);

  const appendLog = (label: string, payload: unknown) => {
    setLogs((prev) => [
      `[${new Date().toLocaleTimeString()}] ${label}:\n${
        typeof payload === "string"
          ? payload
          : JSON.stringify(payload, null, 2)
      }`,
      ...prev.slice(0, LOG_LIMIT - 1),
    ]);
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_ROOT}/alert/monitoring-status`);
      if (!res.ok) {
        throw new Error(`Status ${res.status}`);
      }
      const json = (await res.json()) as MonitoringStatus;
      setData(json);
      appendLog("monitoring-status", json);
      if (selectedTrace && !json.alerts[selectedTrace]) {
        setSelectedTrace(null);
      }
    } catch (err) {
      appendLog("monitoring-status error", stringifyError(err));
    }
  };

  useEffect(() => {
    fetchStatus();
    if (!autoRefresh) return;

    const timer = window.setInterval(() => {
      if (!autoRefresh) return;
      fetchStatus();
    }, STATUS_POLL_INTERVAL);

    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh]);

  const handleControl = async (action: "start" | "stop") => {
    try {
      setControlLoading(true);
      const res = await fetch(
        `${API_ROOT}/alert/${action === "start" ? "start-monitoring" : "stop-monitoring"}`,
        { method: "POST" }
      );
      const json = await res.json();
      appendLog(`${action}-monitoring`, json);
    } catch (err) {
      appendLog(`${action}-monitoring error`, stringifyError(err));
    } finally {
      setControlLoading(false);
      fetchStatus();
    }
  };

  const postHumanAction = async (action: HumanAction) => {
    if (!selectedAlert) return;
    try {
      setLoading(true);
      const payload: Record<string, unknown> = {
        traceId: selectedAlert.traceId,
        action,
      };
      if (selectedAlert.threadId) {
        payload.threadId = selectedAlert.threadId;
      }
      const trimmed = message.trim();
      if (trimmed) {
        payload.message = trimmed;
      }
      if (action === "ignore") {
        payload.ignoreMinutes = ignoreMinutes;
      }

      if (action === "recheck") {
        setStreamingTrace(selectedAlert.traceId);
        setStreamingReport("");
        setHistoryContent(null);
        const res = await fetch(`${API_ROOT}/alert/human-action?stream=true`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify(payload),
        });

        if (!res.ok) {
          throw new Error(`Status ${res.status}`);
        }

        await consumeSSE(res, (event, rawPayload) => {
          appendLog(`human-action:recheck:${event}`, rawPayload);
          if (event === "status") {
            setStreamingReport("Recheck in progress...");
            return;
          }

          if (event === "delta") {
            const delta =
              typeof rawPayload === "object" && rawPayload && "delta" in rawPayload
                ? (rawPayload as Record<string, unknown>).delta
                : rawPayload;
            if (typeof delta === "string") {
              setStreamingReport((prev) => `${prev}${delta}`);
            }
            return;
          }

          if (event === "final") {
            if (
              typeof rawPayload === "object" &&
              rawPayload &&
              "content" in rawPayload &&
              typeof (rawPayload as Record<string, unknown>).content === "string"
            ) {
              setStreamingReport(
                (rawPayload as Record<string, unknown>).content as string
              );
            } else {
              setStreamingReport(JSON.stringify(rawPayload, null, 2));
            }
          }
        });

        setMessage("");
        return;
      }

      const res = await fetch(`${API_ROOT}/alert/human-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      appendLog(`human-action:${action}`, json);
      if (action !== "comment") {
        setMessage("");
      }
    } catch (err) {
      appendLog(`human-action:${action} error`, stringifyError(err));
    } finally {
      setLoading(false);
      await fetchStatus();
      if (action === "recheck") {
        setStreamingTrace(null);
      }
    }
  };

  const fetchHistory = async () => {
    if (!selectedAlert) return;
    try {
      setHistoryLoading(true);
      const res = await fetch(
        `${API_ROOT}/alert/history/${encodeURIComponent(selectedAlert.traceId)}`
      );
      if (!res.ok) {
        throw new Error(`Status ${res.status}`);
      }
      const json = await res.json();
      appendLog("history", json);
      setHistoryContent(JSON.stringify(json.history, null, 2));
    } catch (err) {
      appendLog("history error", stringifyError(err));
      setHistoryContent(stringifyError(err));
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="app__header">
        <div className="header__title">
          <h1>LP Margin Alert Dashboard</h1>
          <span
            className={`status-badge status-badge--${
              data?.status ?? "loading"
            }`}
          >
            {data?.status ?? "loading"}
          </span>
        </div>
        <div className="header__controls">
          <button
            className="primary"
            disabled={controlLoading}
            onClick={() => handleControl("start")}
          >
            Start Monitoring
          </button>
          <button
            disabled={controlLoading}
            onClick={() => handleControl("stop")}
          >
            Stop Monitoring
          </button>
          <button onClick={fetchStatus}>Refresh</button>
          <label className="toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            Auto refresh
          </label>
        </div>
      </header>

      <section className="panel">
        <div className="panel__header">
          <h2>Active Alerts</h2>
          <span className="panel__meta">
            Last fetch:
            {logs.length
              ? ` ${new Date().toLocaleTimeString()}`
              : " —"}
          </span>
        </div>
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>LP</th>
                <th>Severity</th>
                <th>Margin %</th>
                <th>Status</th>
                <th>Trace ID</th>
                <th>Thread ID</th>
                <th>Updated</th>
                <th>Ignore Until</th>
              </tr>
            </thead>
            <tbody>
              {alerts.length ? (
                alerts.map((alert) => {
                  const isSelected = alert.traceId === selectedTrace;
                  return (
                    <tr
                      key={alert.traceId}
                      className={isSelected ? "selected" : ""}
                      onClick={() => setSelectedTrace(alert.traceId)}
                    >
                      <td>{alert.lp}</td>
                      <td className={`severity severity--${alert.severity}`}>
                        {alert.severity}
                      </td>
                      <td>{alert.marginLevel}</td>
                      <td>{STATUS_LABELS[alert.status] ?? alert.status}</td>
                      <td className="mono">{alert.traceId}</td>
                      <td className="mono">
                        {alert.threadId ? alert.threadId : "—"}
                      </td>
                      <td>{new Date(alert.lastUpdatedAt).toLocaleString()}</td>
                      <td>
                        {alert.ignoreUntil
                          ? new Date(alert.ignoreUntil).toLocaleString()
                          : "—"}
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={8} className="empty">
                    No active alerts.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="grid">
        <div className="panel">
          <div className="panel__header">
            <h2>Human Action</h2>
            {selectedAlert ? (
              <span className="panel__meta">
                Selected: <strong>{selectedAlert.lp}</strong>
              </span>
            ) : (
              <span className="panel__meta">Select an alert to act on.</span>
            )}
          </div>
          {selectedAlert ? (
            <div className="form">
              {selectedAlert.status === "recheck_pending" ? (
                <div className="status-note">
                  已复查，但风险阈值仍然偏高。请进一步人工跟进。
                </div>
              ) : null}
              <label>
                Message
                <textarea
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="已补仓，请复查"
                  rows={4}
                />
              </label>
              <label>
                Ignore minutes
                <input
                  type="number"
                  min={1}
                  value={ignoreMinutes}
                  onChange={(event) =>
                    setIgnoreMinutes(Number(event.target.value) || 1)
                  }
                />
              </label>
              <div className="actions">
                <button
                  className="primary"
                  disabled={loading}
                  onClick={() => postHumanAction("recheck")}
                >
                  Recheck
                </button>
                <button
                  disabled={loading || ignoreMinutes < 1}
                  onClick={() => postHumanAction("ignore")}
                >
                  Ignore
                </button>
                <button disabled={loading} onClick={() => postHumanAction("comment")}>
                  Comment
                </button>
              </div>
            </div>
          ) : (
            <p className="placeholder">Select an alert row to enable actions.</p>
          )}
        </div>
        <div className="panel">
          <div className="panel__header">
            <h2>Latest Report</h2>
            {selectedAlert ? (
              <span className="panel__meta">
                Updated: {new Date(selectedAlert.lastUpdatedAt).toLocaleString()}
                {" "}
                <button
                  className="link-button"
                  disabled={!selectedAlert.threadId || historyLoading}
                  onClick={fetchHistory}
                >
                  History
                </button>
              </span>
            ) : (
              <span className="panel__meta">Select an alert to view report.</span>
            )}
          </div>
          {selectedAlert ? (
            streamingTrace === selectedAlert.traceId ? (
              <pre className="report-view streaming">{streamingReport}</pre>
            ) : selectedAlert.latestReport ? (
              <pre className="report-view">{selectedAlert.latestReport}</pre>
            ) : (
              <p className="placeholder">No report captured yet.</p>
            )
          ) : (
            <p className="placeholder">Select an alert row to display its report.</p>
          )}
          {selectedAlert && historyContent && (
            <pre className="history-view">{historyContent}</pre>
          )}
        </div>
        <div className="panel">
          <div className="panel__header">
            <h2>Activity Log</h2>
          </div>
          <pre className="log-view">
            {logs.length ? logs.join("\n\n") : "No activity yet."}
          </pre>
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Thresholds</h2>
        </div>
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Trigger (%)</th>
                <th>Clear (%)</th>
              </tr>
            </thead>
            <tbody>
              {data ? (
                Object.entries(data.thresholds).map(([severity, cfg]) => (
                  <tr key={severity}>
                    <td className={`severity severity--${severity}`}>
                      {severity}
                    </td>
                    <td>{cfg.trigger}</td>
                    <td>{cfg.clear}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={3} className="empty">
                    Loading thresholds…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {data && (
          <p className="footnote">
            Poll interval: {data.interval}s · Blow-up margin level: {" "}
            {data.blowupMarginLevel}%
          </p>
        )}
      </section>
    </div>
  );
}

export default App;

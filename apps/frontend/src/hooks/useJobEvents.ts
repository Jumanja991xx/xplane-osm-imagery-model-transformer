import { useEffect } from "react";
import { api, jobEventsUrl, type JobEvent } from "../api/client";
import { useApp } from "../store";

/** Streams the job's events; state changes and errors trigger a fresh job fetch. */
export function useJobEvents(jobId: string | undefined) {
  useEffect(() => {
    if (!jobId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const refresh = async () => {
      try {
        const job = await api.getJob(jobId);
        if (useApp.getState().job?.id === jobId) useApp.getState().setJob(job);
      } catch {
        /* next event or reconnect will try again */
      }
    };

    const connect = async () => {
      const url = await jobEventsUrl(jobId);
      if (closed) return;
      ws = new WebSocket(url);
      ws.onopen = () => {
        retry = 0;
      };
      ws.onmessage = (msg) => {
        const event = JSON.parse(msg.data) as JobEvent;
        useApp.getState().addEvent(event);
        if (event.type === "state" || event.type === "error") void refresh();
      };
      ws.onclose = () => {
        if (closed) return;
        retry = Math.min(retry + 1, 5);
        timer = setTimeout(connect, 500 * retry);
      };
    };
    void connect();

    // Safety net: while a step runs, also poll. The UI must never hang just
    // because the live channel dropped.
    const poll = setInterval(() => {
      const state = useApp.getState().job?.state;
      if (state === "analyzing" || state === "building" || state === "installing") void refresh();
    }, 2000);

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      clearInterval(poll);
      ws?.close();
    };
  }, [jobId]);
}

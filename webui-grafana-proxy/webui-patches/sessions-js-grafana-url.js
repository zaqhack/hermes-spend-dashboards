function _sessionGrafanaUrl(session) {
  const sid = session && (session.session_id || session.id);
  if (!sid) return null;
  const base = '/grafana-proxy/d/ai-usage-tracking/f09f908e-ai-usage-and-cost-tracking' + '/?var-session=' + encodeURIComponent(sid);
  // Bound the time range to the session's actual lifespan: 5 min before the
  // first activity to 5 min after the last. Sidebar timestamps are unix seconds.
  const PAD_S = 300;
  const startS = Number(session && (session.created_at || session._sidebar_activity_at || 0));
  const endS = Number(session && (session._sidebar_activity_at || session.last_message_at || session.updated_at || 0));
  if (Number.isFinite(startS) && startS > 0 && Number.isFinite(endS) && endS > 0 && endS >= startS) {
    const fromMs = Math.floor((startS - PAD_S) * 1000);
    const toMs = Math.floor((endS + PAD_S) * 1000);
    return base + '&from=' + fromMs + '&to=' + toMs;
  }
  // Fallback: last 7 days to match log retention window
  return base + '&from=now-7d&to=now';
}


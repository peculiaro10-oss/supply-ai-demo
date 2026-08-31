// Cauldra session heartbeat bootstrap for the current authenticated session.
if (window.authToken) { setTimeout(() => { if (typeof startPresenceHeartbeat === 'function') startPresenceHeartbeat(); }, 800); }

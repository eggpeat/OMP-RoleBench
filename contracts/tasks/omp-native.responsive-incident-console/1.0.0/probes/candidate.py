#!/usr/bin/env python3
"""Synthetic designer-role candidate admission probe."""

import json

html = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Relay Incident Console</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="topbar">
    <button class="menu-button" type="button" data-role="mobile-menu" aria-label="Open primary navigation">Menu</button>
    <div class="brand"><span class="brand-mark" aria-hidden="true">R</span><span>Relay Incident Console</span></div>
    <div class="system-state"><span class="state-dot" aria-hidden="true"></span>3 active incidents</div>
  </header>
  <div class="app-shell">
    <aside class="sidebar" data-role="sidebar">
      <nav aria-label="Primary navigation">
        <ul>
          <li><a href="#overview">Overview</a></li>
          <li><a href="#incidents" aria-current="page">Incidents</a></li>
          <li><a href="#services">Services</a></li>
          <li><a href="#runbooks">Runbooks</a></li>
        </ul>
      </nav>
      <p class="on-call"><span>On call</span><strong>Platform team</strong></p>
    </aside>
    <main id="incidents">
      <section class="page-heading" aria-labelledby="page-title">
        <div><p class="eyebrow">Live operations</p><h1 id="page-title">Active incidents</h1><p class="lede">Prioritized signals requiring on-call attention.</p></div>
        <form class="filter-form"><label for="service-filter">Service</label><select id="service-filter" data-role="primary-action"><option>All services</option><option>API</option><option>Ingest</option><option>Storage</option></select></form>
      </section>
      <section aria-label="Incident list" class="incident-grid" data-role="incident-grid">
        <article class="incident incident-critical">
          <div class="card-meta"><span class="severity">critical</span><span>12m</span></div>
          <p class="incident-id">INC-241 · API</p><h2>API latency above SLO</h2>
          <details><summary>Timeline</summary><p data-timeline>11:42 alert · 11:46 mitigation started</p></details>
        </article>
        <article class="incident incident-warning">
          <div class="card-meta"><span class="severity">warning</span><span>31m</span></div>
          <p class="incident-id">INC-238 · Ingest</p><h2>Ingest retry queue growing</h2>
          <details><summary>Timeline</summary><p data-timeline>11:23 alert · 11:35 owner assigned</p></details>
        </article>
        <article class="incident incident-watch">
          <div class="card-meta"><span class="severity">watch</span><span>1h</span></div>
          <p class="incident-id">INC-233 · Storage</p><h2>Replica lag elevated</h2>
          <details><summary>Timeline</summary><p data-timeline>10:54 alert · 11:18 lag stabilizing</p></details>
        </article>
      </section>
    </main>
  </div>
</body>
</html>'''

css = '''
:root { color-scheme: dark; --canvas: #0b1020; --panel: #121a2f; --raised: #18233e; --line: #2a385c; --text: #f2f5ff; --muted: #aeb9d5; --accent: #78a9ff; }
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body { background: var(--canvas); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }
button, select { font: inherit; }
.topbar { min-height: 68px; display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 0 28px; border-bottom: 1px solid var(--line); background: rgba(18, 26, 47, .96); }
.brand { display: flex; align-items: center; gap: 12px; font-weight: 760; letter-spacing: -.02em; }
.brand-mark { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 10px; background: var(--accent); color: #071126; }
.system-state { display: flex; align-items: center; gap: 9px; color: var(--muted); font-size: .9rem; }
.state-dot { width: 9px; height: 9px; border-radius: 50%; background: #ff6b7d; box-shadow: 0 0 0 4px rgba(255,107,125,.16); }
.menu-button { display: none; min-width: 44px; min-height: 44px; padding: 0 14px; border: 1px solid var(--line); border-radius: 9px; background: var(--raised); color: var(--text); }
.app-shell { display: grid; grid-template-columns: 230px minmax(0, 1fr); min-height: calc(100vh - 68px); }
.sidebar { padding: 28px 18px; border-right: 1px solid var(--line); background: #0e1528; }
.sidebar ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.sidebar a { display: block; padding: 11px 13px; border-radius: 9px; color: var(--muted); text-decoration: none; }
.sidebar a[aria-current="page"] { background: var(--raised); color: var(--text); box-shadow: inset 3px 0 var(--accent); }
.on-call { display: grid; gap: 3px; margin: 44px 13px 0; color: var(--muted); font-size: .82rem; }
.on-call strong { color: var(--text); font-size: .94rem; }
main { min-width: 0; padding: 36px; }
.page-heading { display: flex; align-items: end; justify-content: space-between; gap: 28px; margin-bottom: 28px; }
.eyebrow { margin: 0 0 5px; color: var(--accent); font-size: .78rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
h1 { margin: 0; font-size: clamp(2rem, 4vw, 3.3rem); line-height: 1.05; letter-spacing: -.05em; }
.lede { margin: 10px 0 0; color: var(--muted); }
.filter-form { display: grid; gap: 7px; color: var(--muted); font-size: .82rem; font-weight: 700; }
.filter-form select { min-width: 184px; min-height: 44px; padding: 0 38px 0 12px; border: 1px solid #41527e; border-radius: 9px; background: var(--raised); color: var(--text); }
.incident-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.incident { min-width: 0; padding: 22px; border: 1px solid var(--line); border-top-width: 3px; border-radius: 14px; background: var(--panel); box-shadow: 0 16px 36px rgba(0,0,0,.18); }
.incident-critical { border-top-color: #ff6b7d; }
.incident-warning { border-top-color: #ffc857; }
.incident-watch { border-top-color: #6bd9b4; }
.card-meta { display: flex; justify-content: space-between; gap: 16px; color: var(--muted); font-size: .8rem; }
.severity { color: var(--text); font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.incident-id { margin: 26px 0 6px; color: var(--accent); font: 700 .82rem/1.3 ui-monospace, monospace; }
h2 { min-height: 3.2em; margin: 0 0 24px; font-size: 1.18rem; line-height: 1.35; }
details { border-top: 1px solid var(--line); padding-top: 14px; color: var(--muted); }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; color: var(--text); font-weight: 700; }
details p { margin: 8px 0 0; font-size: .88rem; }
:focus-visible { outline: 3px solid #f8d66d; outline-offset: 3px; }
@media (max-width: 700px) {
  .topbar { min-height: 64px; padding: 0 16px; }
  .menu-button { display: inline-flex; align-items: center; justify-content: center; }
  .system-state { display: none; }
  .app-shell { display: block; min-height: calc(100vh - 64px); }
  .sidebar { display: none; }
  main { padding: 24px 16px 36px; }
  .page-heading { align-items: stretch; flex-direction: column; gap: 20px; }
  .filter-form select { width: 100%; }
  .incident-grid { grid-template-columns: minmax(0, 1fr); }
  h1 { font-size: 2.25rem; }
  h2 { min-height: auto; }
}
'''

print(json.dumps({"schema_version": "rolebench.ui-implementation/v1", "files": {"index.html": html, "styles.css": css}}, sort_keys=True, separators=(",", ":")))

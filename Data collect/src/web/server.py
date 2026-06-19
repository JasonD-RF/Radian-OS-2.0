"""
Radian OS 2.0 — Live Data Dashboard
Run:  python -m src.web.server --config config/collectors.local.yaml
Open: http://localhost:8765
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import yaml
from aiohttp import web

PORT = 8765

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radian OS 2.0</title>
<style>
:root {
  --bg: #0d1117; --card: #161b22; --border: #30363d;
  --green: #3fb950; --yellow: #e3b341; --red: #f85149;
  --text: #c9d1d9; --dim: #8b949e; --blue: #58a6ff; --orange: #ffa657;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text);
       font-family: 'Segoe UI', system-ui, sans-serif; }

header {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 24px; border-bottom: 1px solid var(--border);
  background: var(--card); position: sticky; top: 0; z-index: 10;
}
.logo { font-size: 1rem; font-weight: 700; color: var(--blue); letter-spacing: -0.02em; }
.logo span { color: var(--dim); font-weight: 400; }
#conn-status { margin-left: auto; font-size: 0.78rem; display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--dim); }
.dot.live { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot.dead { background: var(--red); }
#event-count { font-size: 0.75rem; color: var(--dim); }

main { padding: 20px 24px; display: grid; gap: 20px; }
.device-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; overflow: hidden;
}
.card-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
}
.card-name { font-weight: 600; font-size: 0.9rem; }
.badge {
  font-size: 0.7rem; font-weight: 700; padding: 2px 9px;
  border-radius: 20px; letter-spacing: 0.04em;
}
.badge-kuka    { background: #1a2f4a; color: var(--blue); }
.badge-fronius { background: #3a2510; color: var(--orange); }
.card-ts { font-size: 0.68rem; color: var(--dim); margin-left: auto; padding-left: 10px; font-variant-numeric: tabular-nums; }

table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
td { padding: 5px 16px; border-bottom: 1px solid #1c2128; vertical-align: middle; }
td:first-child { color: var(--dim); width: 46%; }
td.v-bool-true  { color: var(--green); font-weight: 600; }
td.v-bool-false { color: #4a5568; }
td.v-num  { color: var(--blue); font-variant-numeric: tabular-nums; }
td.v-str  { color: var(--yellow); }
td.flash  { animation: flash 0.5s ease; }
@keyframes flash { from { background: rgba(63,185,80,.18); } to {} }

.arc-on { border-left: 3px solid var(--green); }
.arc-off { border-left: 3px solid var(--border); }

.fronius-sections { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
.fronius-group { border-right: 1px solid var(--border); }
.fronius-group:nth-child(even) { border-right: none; }
.fronius-group-title {
  padding: 5px 12px; font-size: 0.65rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em; color: var(--dim);
  background: #0d1117; border-bottom: 1px solid var(--border);
  border-top: 1px solid var(--border);
}
.fronius-group:first-child .fronius-group-title { border-top: none; }
.fronius-group:nth-child(2) .fronius-group-title { border-top: none; }
.fronius-tbl { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
.fronius-tbl td { padding: 3px 12px; border-bottom: 1px solid #1c2128; }
.fronius-tbl td:first-child { color: var(--dim); white-space: nowrap; }
.fronius-tbl td:last-child { text-align: right; font-variant-numeric: tabular-nums; }

section.frames-section h2 {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--dim); margin-bottom: 10px;
}
.frames-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.frame-card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; overflow: hidden;
}
.frame-card-head {
  padding: 10px 16px; border-bottom: 1px solid var(--border);
  font-size: 0.82rem; font-weight: 600; color: var(--dim);
  display: flex; justify-content: space-between; align-items: center;
}
.frame-sub-head {
  padding: 6px 16px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  font-size: 0.72rem; font-weight: 600; color: var(--dim);
  display: flex; align-items: center;
}
.frame-num-badge {
  font-size: 0.68rem; font-weight: 700; padding: 1px 8px;
  border-radius: 12px; background: #1a2f1a; color: var(--green);
}
.frame-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; table-layout: fixed; }
.frame-table th {
  padding: 5px 10px; border-bottom: 1px solid var(--border);
  color: var(--dim); font-weight: 600; text-align: right; white-space: nowrap;
  width: 16.666%;
}
.frame-table td { padding: 5px 10px; border-bottom: 1px solid #1c2128; text-align: right; font-variant-numeric: tabular-nums; color: var(--green); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

section.log-section h2 {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--dim); margin-bottom: 10px;
}
#log {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; height: 200px; overflow-y: auto;
  font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 0.76rem;
}
.log-row {
  display: grid;
  grid-template-columns: 95px 145px 165px 1fr;
  padding: 3px 14px; border-bottom: 1px solid #1c2128; line-height: 1.7;
}
.log-row:hover { background: #1c2128; }
.l-ts  { color: var(--dim); }
.l-dev { }
.l-dev.kuka    { color: var(--blue); }
.l-dev.fronius { color: var(--orange); }
.l-key { color: var(--green); }
.l-val { color: var(--text); }
.l-hb  { color: #3a3f4a; font-style: italic; grid-column: 3 / 5; }

#syslog {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; height: 200px; overflow-y: auto;
  font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 0.76rem;
}
.syslog-row {
  display: grid;
  grid-template-columns: 95px 72px 140px 1fr;
  padding: 3px 14px; border-bottom: 1px solid #1c2128; line-height: 1.7;
}
.syslog-row:hover { background: #1c2128; }
.sl-ts     { color: var(--dim); }
.sl-level  { font-weight: 700; }
.sl-info   { color: var(--dim); }
.sl-warn   { color: var(--yellow); }
.sl-err    { color: var(--red); }
.sl-crit   { color: var(--red); text-transform: uppercase; }
.sl-logger { color: var(--blue); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sl-msg    { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── 3D Toolpath Visualizer ─────────────────────────────────────────────── */
.toolpath-section {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  overflow: hidden;
}
.toolpath-header {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap;
}
.toolpath-title { font-weight: 700; font-size: 0.9rem; color: var(--blue); margin-right: 4px; white-space: nowrap; }
.toolpath-controls { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.tp-sep { color: var(--border); padding: 0 2px; }
.tp-select, .tp-btn {
  background: #0d1117; color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 10px; font-size: 0.78rem; cursor: pointer;
  font-family: inherit;
}
.tp-select:focus, .tp-btn:hover { border-color: var(--blue); outline: none; }
.tp-btn-danger { background: #1a0808; color: var(--red); border-color: #3a1010; }
.tp-btn-danger:hover { border-color: var(--red); }
.tp-label { font-size: 0.78rem; color: var(--dim); display: flex; align-items: center; gap: 4px; cursor: pointer; white-space: nowrap; }
.tp-label input { cursor: pointer; accent-color: var(--green); }
.tp-radio-group { display: flex; gap: 10px; }
.tp-stat { font-size: 0.75rem; color: var(--dim); white-space: nowrap; margin-left: 4px; }
#tp-canvas-wrap { position: relative; height: 620px; background: #090d12; }
#tp-canvas { width: 100%; height: 100%; display: block; }
#tp-overlay {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--dim); font-size: 0.9rem; pointer-events: none;
}
#tp-scrubber-wrap {
  position: absolute; bottom: 0; left: 0; right: 0;
  padding: 10px 14px 13px;
  background: linear-gradient(transparent, rgba(0,0,0,.72));
  display: none;
}
#tp-scrubber {
  -webkit-appearance: none; appearance: none;
  width: 100%; height: 4px; border-radius: 2px; outline: none; cursor: pointer;
  background: linear-gradient(to right,
    #3fb950 0%, #3fb950 var(--pct,0%),
    rgba(255,255,255,.18) var(--pct,0%), rgba(255,255,255,.18) 100%);
}
#tp-scrubber::-webkit-slider-thumb {
  -webkit-appearance: none; width: 14px; height: 14px;
  border-radius: 50%; background: #fff; cursor: grab; transition: transform .1s;
}
#tp-scrubber:active::-webkit-slider-thumb { cursor: grabbing; transform: scale(1.25); }
#tp-scrubber::-moz-range-thumb {
  width: 14px; height: 14px; border-radius: 50%;
  background: #fff; cursor: grab; border: none;
}

/* ── Delete job confirmation modal ───────────────────────────────────────── */
.tp-modal-backdrop {
  position: fixed; inset: 0; background: rgba(0,0,0,.75); z-index: 1000;
  display: flex; align-items: center; justify-content: center;
}
.tp-modal {
  background: var(--card); border: 1px solid var(--red); border-radius: 12px;
  padding: 32px 36px; max-width: 440px; width: 90%; text-align: center;
}
.tp-modal-msg { font-size: 0.95rem; line-height: 1.6; margin-bottom: 24px; }
.tp-modal-msg strong { color: var(--red); display: block; margin-top: 6px; }
.tp-modal-btns { display: flex; gap: 14px; justify-content: center; }
.tp-confirm-yes {
  background: #2a0808; color: var(--red); border: 1px solid var(--red);
  padding: 9px 22px; border-radius: 7px; cursor: pointer; font-weight: 700; font-size: 0.85rem;
}
.tp-confirm-yes:hover { background: #3a1010; }
.tp-confirm-no {
  background: #0d1a0d; color: var(--green); border: 1px solid var(--green);
  padding: 9px 22px; border-radius: 7px; cursor: pointer; font-size: 0.85rem;
}
.tp-confirm-no:hover { background: #1a2f1a; }
</style>
<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.1/build/three.module.min.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.1/examples/jsm/"}}
</script>
</head>
<body>
<header>
  <span class="logo">Radian OS <span>2.0</span></span>
  <span id="event-count"></span>
  <div id="conn-status">
    <div class="dot" id="dot"></div>
    <span id="conn-label">Connecting…</span>
  </div>
</header>

<main>
  <div class="device-grid">
    <div class="card arc-off" id="card-chesty_kuka">
      <div class="card-head">
        <span class="card-name">chesty &mdash; KUKA</span>
        <span class="badge badge-kuka">OPC UA · 63 nodes</span>
        <span class="card-ts" id="ts-chesty_kuka"></span>
      </div>
      <table id="tbl-chesty_kuka"></table>
    </div>
    <div class="card arc-off" id="card-mattis_kuka">
      <div class="card-head">
        <span class="card-name">mattis &mdash; KUKA</span>
        <span class="badge badge-kuka">OPC UA · 63 nodes</span>
        <span class="card-ts" id="ts-mattis_kuka"></span>
      </div>
      <table id="tbl-mattis_kuka"></table>
    </div>
    <div class="card arc-off" id="card-chesty_fronius">
      <div class="card-head">
        <span class="card-name">chesty &mdash; Fronius</span>
        <span class="badge badge-fronius">OPC UA · 62 nodes</span>
        <span class="card-ts" id="ts-chesty_fronius"></span>
      </div>
      <div id="frn-chesty_fronius" class="fronius-sections"></div>
    </div>
    <div class="card arc-off" id="card-mattis_fronius">
      <div class="card-head">
        <span class="card-name">mattis &mdash; Fronius</span>
        <span class="badge badge-fronius">OPC UA · 62 nodes</span>
        <span class="card-ts" id="ts-mattis_fronius"></span>
      </div>
      <div id="frn-mattis_fronius" class="fronius-sections"></div>
    </div>
  </div>

  <section class="frames-section">
    <h2>Active Coordinate Frames</h2>
    <div class="frames-grid">
      <div class="frame-card">
        <div class="frame-card-head">chesty</div>
        <div class="frame-sub-head">Base Frame &nbsp;<span id="frame-base-num-chesty_kuka" class="frame-num-badge">—</span></div>
        <table class="frame-table" id="btbl-chesty_kuka"></table>
        <div class="frame-sub-head">Tool Frame &nbsp;<span id="frame-tool-num-chesty_kuka" class="frame-num-badge">—</span></div>
        <table class="frame-table" id="ttbl-chesty_kuka"></table>
      </div>
      <div class="frame-card">
        <div class="frame-card-head">mattis</div>
        <div class="frame-sub-head">Base Frame &nbsp;<span id="frame-base-num-mattis_kuka" class="frame-num-badge">—</span></div>
        <table class="frame-table" id="btbl-mattis_kuka"></table>
        <div class="frame-sub-head">Tool Frame &nbsp;<span id="frame-tool-num-mattis_kuka" class="frame-num-badge">—</span></div>
        <table class="frame-table" id="ttbl-mattis_kuka"></table>
      </div>
    </div>
  </section>

  <section class="toolpath-section">
    <div class="toolpath-header">
      <span class="toolpath-title">3D Toolpath Visualizer</span>
      <div class="toolpath-controls">
        <select id="tp-robot" class="tp-select">
          <option value="chesty_kuka">Chesty</option>
          <option value="mattis_kuka">Mattis</option>
        </select>
        <select id="tp-job" class="tp-select"><option value="">&#8212; select job &#8212;</option></select>
        <select id="tp-tool" class="tp-select"><option value="">All Tools</option></select>
        <label class="tp-label"><input type="checkbox" id="tp-arc-only"> Weld only</label>
        <label class="tp-label"><input type="checkbox" id="tp-grid" checked> Grid</label>
        <label class="tp-label"><input type="checkbox" id="tp-axes" checked> Origin</label>
        <label class="tp-label"><input type="checkbox" id="tp-tcp" checked> TCP</label>
        <span class="tp-sep">|</span>
        <div class="tp-radio-group">
          <label class="tp-label"><input type="radio" name="tp-mode" value="live" checked> Live</label>
          <label class="tp-label"><input type="radio" name="tp-mode" value="playback"> Playback</label>
        </div>
        <span class="tp-sep">|</span>
        <select id="tp-speed" class="tp-select">
          <option value="1">1&#xD7; speed</option>
          <option value="2">2&#xD7;</option>
          <option value="5">5&#xD7;</option>
          <option value="10">10&#xD7;</option>
          <option value="50">50&#xD7;</option>
        </select>
        <button id="tp-play" class="tp-btn">&#9654; Play</button>
        <button id="tp-reset" class="tp-btn">&#x27F3; Fit</button>
        <button id="tp-export-csv" class="tp-btn">&#8595; CSV</button>
        <button id="tp-delete-job" class="tp-btn tp-btn-danger">&#x1F5D1;</button>
        <span id="tp-stats" class="tp-stat"></span>
      </div>
    </div>
    <div id="tp-canvas-wrap">
      <canvas id="tp-canvas"></canvas>
      <div id="tp-overlay">Select a robot and job to begin</div>
      <div id="tp-scrubber-wrap">
        <input type="range" id="tp-scrubber" min="0" max="1000" value="0" step="1">
      </div>
    </div>
  </section>

  <div id="tp-confirm-modal" class="tp-modal-backdrop" style="display:none">
    <div class="tp-modal">
      <p class="tp-modal-msg">
        Are you sure you want to delete this job?
        <strong>This data can NOT be recovered.</strong>
      </p>
      <div class="tp-modal-btns">
        <button id="tp-confirm-yes" class="tp-confirm-yes">Yes, I am sure.</button>
        <button id="tp-confirm-no"  class="tp-confirm-no">No, do not delete job.</button>
      </div>
    </div>
  </div>

  <section class="log-section">
    <h2>System Log</h2>
    <div id="syslog"></div>
  </section>

  <section class="log-section">
    <h2>Live Event Log</h2>
    <div id="log"></div>
  </section>
</main>

<script>
const KUKA_ROWS = [
  // -- Program / motion -------------------------------------------------------
  ['program_state',    'Program State'],
  ['in_motion',        'In Motion'],
  ['on_path',          'On Path'],
  ['speed_override',   'Speed Override'],
  ['ov_pro',           'Program Override %'],
  ['peri_rdy',         'Periphery Ready'],
  ['drives_off',       'Drives Off'],
  ['task_program_name','Program File'],
  ['project_name',     'Project'],
  // -- TCP position -----------------------------------------------------------
  ['tcp_x','TCP X (mm)'], ['tcp_y','TCP Y (mm)'], ['tcp_z','TCP Z (mm)'],
  // -- Joint axes -------------------------------------------------------------
  ['a1','A1°'], ['a2','A2°'], ['a3','A3°'],
  ['a4','A4°'], ['a5','A5°'], ['a6','A6°'],
  // -- Pyrometer --------------------------------------------------------------
  ['pyrometer_temp_c',    'Pyrometer Filt (°C)'],
  ['pyrometer_temp_raw_c','Pyrometer Raw (°C)'],
  // -- ArcTech print state (live when program running) -----------------------
  ['print_active',     'Print Active'],
  ['print_resume',     'Print Resume'],
  ['print_complete',   'Print Complete'],
  ['stop_cycle',       'Stop Cycle'],
  ['new_print',        'New Print'],
  ['active_layer',     'Active Layer'],
  ['next_layer',       'Next Layer'],
  ['layer_count',      'Layer Count'],
  ['active_layer_seam','Layer Seam'],
  ['next_seam',        'Next Seam'],
  ['active_total_seam','Total Seam #'],
  ['total_seam_count', 'Total Seams Done'],
  ['seams_in_layer',   'Seams In Layer'],
  ['layer_rerun',      'Layer Rerun'],
  ['skip_layer',       'Skip Layer'],
  ['interpass_cleaning','Interpass Clean'],
  ['last_error',       'Last Error'],
  ['vel_cp',           'Cart Vel (mm/s)'],
  // -- Cabinet health ---------------------------------------------------------
  ['fan_speed_outside','Fan Outside (RPM)'],
  ['fan_speed_kpc',    'Fan KPC (RPM)'],
  ['ups_state',        'UPS State'],
  // -- Safety -----------------------------------------------------------------
  ['operational_mode', 'Op Mode'],
  ['emergency_stop',   'E-Stop'],
  ['protective_stop',  'Prot. Stop'],
  // -- Active base / tool ------------------------------------------------------
  ['base_num',     'Active Base #'],
  ['tool_num',     'Active Tool #'],
  ['in_control',   'In Control'],
  ['program_line', 'Program Line'],
];
const FRONIUS_SECTIONS = [
  { title: 'Arc State', rows: [
    ['process_active',          'Arc On'],
    ['arc_stable',              'Arc Stable'],
    ['current_flow',            'Current Flowing'],
    ['process_mainphase',       'Main Phase'],
    ['alarm',                   'Alarm'],
    ['safety_status',           'Safety Status'],
    ['wireend',                 'Wire End'],
    ['penetration_stab_status', 'Penetration Stab'],
    ['arc_length_stab_status',  'Arc Length Stab'],
  ]},
  { title: 'Live Measurements', rows: [
    ['display_current_a',   'Current A (display)'],
    ['display_voltage_v',   'Voltage V (display)'],
    ['wire_feed_mpm',       'Wire Feed m/min (disp)'],
    ['power_w',             'Power kW (display)'],
    ['display_energy_kj',   'Energy kJ (display)'],
    ['actual_weldingtime',  'Weld Time s'],
    ['current_a',           'Current A (RT)'],
    ['voltage_v',           'Voltage V (RT)'],
    ['actual_wfs_mpm',      'Wire Feed m/min (RT)'],
    ['actual_power_kw',     'Power kW (RT)'],
    ['actual_gasflow_lpm',  'Gas Flow L/min'],
    ['wirebuffer',          'Wire Buffer'],
  ]},
  { title: 'Job & Settings', rows: [
    ['job_name',             'Job Name'],
    ['job_number',           'Job #'],
    ['job_revision',         'Job Rev'],
    ['job_mode',             'Job Mode'],
    ['seam_number',          'Seam #'],
    ['welding_mode',         'Weld Mode'],
    ['current_recomm_a',     'Current Recomm (A)'],
    ['voltage_recomm_v',     'Voltage Recomm (V)'],
    ['wfs_commanded_mpm',    'WFS Cmd (m/min)'],
    ['gas_setpoint_lpm',     'Gas Set (L/min)'],
    ['arclength_correction', 'Arc Length Corr'],
    ['pulsdynamic_correction','Pulse Dynamic'],
    ['penetration_stab_set', 'Penetration Stab'],
    ['arc_length_stab_set',  'Arc Length Stab'],
    ['sfi_enabled',          'SFI'],
    ['start_current_a',      'Start I (A)'],
    ['start_current_time_s', 'Start I Time (s)'],
    ['slope_1_s',            'Slope 1 (s)'],
    ['end_current_a',        'End I (A)'],
    ['end_current_time_s',   'End I Time (s)'],
    ['slope_2_s',            'Slope 2 (s)'],
    ['gas_preflow_s',        'Gas Preflow (s)'],
    ['gas_postflow_s',       'Gas Postflow (s)'],
    ['synchropulse_enabled', 'SynchroP On'],
    ['synchropulse_freq_hz', 'SynchroP Freq (Hz)'],
    ['synchropulse_delta',   'SynchroP ΔFeeder'],
    ['synchropulse_duty_pct','SynchroP Duty %'],
    ['part_serial',          'Part S/N'],
    ['part_item',            'Part Item #'],
    ['part_version',         'Part Version'],
  ]},
  { title: 'System Health', rows: [
    ['cooler_temp_c',     'Cooler Temp (°C)'],
    ['cooler_flow_lpm',   'Cooler Flow (L/min)'],
    ['cooler_mode',       'Cooler Mode'],
    ['motor_force_m1',    'Motor Force M1'],
    ['motor_force_m2',    'Motor Force M2'],
    ['total_arc_time_h',  'Total Arc Time (h)'],
    ['total_power_on_h',  'Total Power-On (h)'],
    ['total_wire_length', 'Total Wire (m)'],
    ['total_gas_l',       'Total Gas (L)'],
    ['serial_number',     'Serial #'],
    ['firmware_version',  'Firmware'],
  ]},
];

const DEVICE_ROWS = {
  chesty_kuka: KUKA_ROWS, mattis_kuka: KUKA_ROWS,
};

function renderFronius(deviceId, values) {
  const container = document.getElementById('frn-' + deviceId);
  if (!container) return;
  let html = '';
  for (const sec of FRONIUS_SECTIONS) {
    html += `<div class="fronius-group"><div class="fronius-group-title">${sec.title}</div><table class="fronius-tbl">`;
    for (const [k, label] of sec.rows) {
      const v = values[k];
      html += `<tr><td>${label}</td><td class="${vClass(v)}">${fmt(v)}</td></tr>`;
    }
    html += '</table></div>';
  }
  container.innerHTML = html;
  // Arc-on border
  const card = document.getElementById('card-' + deviceId);
  if (card) {
    card.classList.toggle('arc-on',  !!values['process_active']);
    card.classList.toggle('arc-off', !values['process_active']);
  }
}

const snaps = {};
// Last numeric value written to each cell — keyed as "deviceId:key"
const lastNum = {};
let evCount = 0;

// Keys that carry high-frequency analog noise — deadband applies, no flash.
const ANALOG_KEYS = new Set([
  'a1','a2','a3','a4','a5','a6',
  'tcp_x','tcp_y','tcp_z','tcp_a','tcp_b','tcp_c',
  'pyrometer_temp_c','pyrometer_temp_raw_c',
  'fan_speed_outside','fan_speed_inside','fan_speed_kpc',
  'vel_cp','speed_override',
]);
// Minimum change required to update an analog cell (degrees / mm / RPM)
const DEADBAND = 0.05;

function fmt(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function vClass(v) {
  if (typeof v === 'boolean') return v ? 'v-bool-true' : 'v-bool-false';
  if (typeof v === 'number')  return 'v-num';
  if (typeof v === 'string' && v !== '—') return 'v-str';
  return '';
}

function renderTable(deviceId, values, changedKey) {
  const rows = DEVICE_ROWS[deviceId] || [];
  const tbl  = document.getElementById('tbl-' + deviceId);
  if (!tbl) return;

  // First paint — build the table once, tag each row with data-key.
  if (tbl.rows.length === 0) {
    tbl.innerHTML = rows.map(([k, label]) => {
      const v = values[k];
      if (typeof v === 'number') lastNum[deviceId + ':' + k] = v;
      return `<tr data-key="${k}"><td>${label}</td><td class="${vClass(v)}">${fmt(v)}</td></tr>`;
    }).join('');
    return;
  }

  // Subsequent updates: only touch the cells that actually need to change.
  for (const row of tbl.rows) {
    const k    = row.dataset.key;
    const v    = values[k];
    const cell = row.cells[1];
    const numKey = deviceId + ':' + k;

    if (ANALOG_KEYS.has(k) && typeof v === 'number') {
      // Deadband suppresses servo jitter on rapid OPC change events.
      // On heartbeats (changedKey===null) bypass deadband so pyrometer and
      // axis values always refresh with the latest snapshot every 5 seconds.
      const prev = lastNum[numKey];
      if (changedKey !== null && prev !== undefined && Math.abs(v - prev) < DEADBAND) continue;
      lastNum[numKey] = v;
      cell.textContent = fmt(v);
      cell.className   = vClass(v);
    } else {
      // For state/discrete keys: exact string comparison, flash on change.
      const text = fmt(v);
      if (cell.textContent === text) continue;
      cell.textContent = text;
      cell.className   = vClass(v);
      if (k === changedKey) {
        cell.classList.add('flash');
        setTimeout(() => cell.classList.remove('flash'), 600);
      }
    }
  }

}

// Track which devices have had their initial paint so heartbeats
// don't trigger a redundant re-render.
const initialized = {};

const FRAME_COORD_HEADER = '<tr><th>X (mm)</th><th>Y (mm)</th><th>Z (mm)</th><th>A (°)</th><th>B (°)</th><th>C (°)</th></tr>';

function fmtCoord(v) {
  if (v === null || v === undefined) return '—';
  return (typeof v === 'number') ? v.toFixed(3) : String(v);
}

function renderSingleFrame(tblId, frame) {
  const tbl = document.getElementById(tblId);
  if (!tbl) return;
  const noData = `<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:10px">awaiting data…</td></tr>`;
  if (!frame || typeof frame !== 'object') {
    tbl.innerHTML = FRAME_COORD_HEADER + noData;
    return;
  }
  tbl.innerHTML = FRAME_COORD_HEADER + `<tr>
    <td>${fmtCoord(frame.x)}</td><td>${fmtCoord(frame.y)}</td><td>${fmtCoord(frame.z)}</td>
    <td>${fmtCoord(frame.a)}</td><td>${fmtCoord(frame.b)}</td><td>${fmtCoord(frame.c)}</td>
  </tr>`;
}

function renderFrames(deviceId, values) {
  const baseNum = values['base_num'];
  const toolNum = values['tool_num'];
  const bn = document.getElementById('frame-base-num-' + deviceId);
  if (bn) bn.textContent = (baseNum !== null && baseNum !== undefined) ? '#' + baseNum : '—';
  const tn = document.getElementById('frame-tool-num-' + deviceId);
  if (tn) tn.textContent = (toolNum !== null && toolNum !== undefined) ? '#' + toolNum : '—';
  renderSingleFrame('btbl-' + deviceId, values['active_base_frame']);
  renderSingleFrame('ttbl-' + deviceId, values['active_tool_frame']);
}


const syslogEl = document.getElementById('syslog');
const LEVEL_CLASS = { INFO: 'sl-info', WARNING: 'sl-warn', ERROR: 'sl-err', CRITICAL: 'sl-crit' };
function addSysLog(ts, level, values) {
  const t = ts.split('T')[1]?.slice(0, 12) ?? ts;
  const row = document.createElement('div');
  row.className = 'syslog-row';
  const lc = LEVEL_CLASS[level] || 'sl-info';
  row.innerHTML =
    `<span class="sl-ts">${t}</span>`
    + `<span class="sl-level ${lc}">${level || ''}</span>`
    + `<span class="sl-logger">${values.logger || ''}</span>`
    + `<span class="sl-msg">${values.message || ''}</span>`;
  syslogEl.prepend(row);
  while (syslogEl.children.length > 200) syslogEl.removeChild(syslogEl.lastChild);
}

const logEl = document.getElementById('log');
function addLog(ts, deviceId, changedKey, values) {
  const t = ts.split('T')[1]?.slice(0, 12) ?? ts;
  const isKuka = deviceId.includes('kuka');
  const devClass = isKuka ? 'kuka' : 'fronius';
  const row = document.createElement('div');
  row.className = 'log-row';
  if (!changedKey) {
    row.innerHTML = `<span class="l-ts">${t}</span>`
      + `<span class="l-dev ${devClass}">${deviceId}</span>`
      + `<span class="l-hb">— heartbeat —</span>`;
  } else {
    const val = fmt(values[changedKey]);
    row.innerHTML = `<span class="l-ts">${t}</span>`
      + `<span class="l-dev ${devClass}">${deviceId}</span>`
      + `<span class="l-key">${changedKey}</span>`
      + `<span class="l-val">${val}</span>`;
  }
  logEl.prepend(row);
  while (logEl.children.length > 300) logEl.removeChild(logEl.lastChild);
}

const dot   = document.getElementById('dot');
const label = document.getElementById('conn-label');
const cntEl = document.getElementById('event-count');

let es;
let lastEventTime = Date.now();

function connectSSE() {
  if (es) { try { es.close(); } catch (_) {} }
  es = new EventSource('/events');
  es.onopen  = () => { dot.className = 'dot live'; label.textContent = 'Live'; };
  es.onerror = () => { dot.className = 'dot dead'; label.textContent = 'Reconnecting…'; };
  es.onmessage = e => {
    lastEventTime = Date.now();
    const d = JSON.parse(e.data);
    evCount++;
    if (evCount % 10 === 0)
      cntEl.textContent = evCount.toLocaleString() + ' events received';

    if (d.device_id === 'system') {
      addSysLog(d.ts, d.changed_key, d.values);
      return;
    }

    snaps[d.device_id] = Object.assign(snaps[d.device_id] || {}, d.values);
    if (d.device_id.endsWith('_fronius')) {
      renderFronius(d.device_id, snaps[d.device_id]);
    } else {
      renderTable(d.device_id, snaps[d.device_id], d.changed_key);
    }
    if (d.device_id.endsWith('_kuka')) renderFrames(d.device_id, snaps[d.device_id]);
    initialized[d.device_id] = true;

    const tsEl = document.getElementById('ts-' + d.device_id);
    if (tsEl) tsEl.textContent = d.ts.split('T')[1]?.slice(0, 8) + ' UTC';

    addLog(d.ts, d.device_id, d.changed_key, d.values);
  };
}

connectSSE();

// Watchdog: keepalive fires every 30s, so 45s with no event means the
// connection is silently dead. Force a clean reconnect.
setInterval(() => {
  const stale = Date.now() - lastEventTime;
  if (stale > 45000) {
    lastEventTime = Date.now(); // reset before reconnect to avoid repeat fires
    addSysLog(new Date().toISOString(), 'WARNING', {
      logger: 'browser.watchdog',
      message: `SSE silent for ${Math.round(stale / 1000)}s — reconnecting`,
    });
    connectSSE();
  }
}, 10000);
</script>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ── Scene setup ───────────────────────────────────────────────────────────
const canvas  = document.getElementById('tp-canvas');
const wrap    = document.getElementById('tp-canvas-wrap');
const overlay = document.getElementById('tp-overlay');

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x090d12);

const scene  = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 1, 500000);
camera.position.set(0, 2000, 3000);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

const axes = new THREE.AxesHelper(500);
scene.add(axes);
const grid = new THREE.GridHelper(10000, 20, 0x1c2128, 0x1c2128);
scene.add(grid);

// Live position marker — inverted cone at the robot's current TCP
const liveDot = new THREE.Mesh(
  new THREE.ConeGeometry(14, 40, 24),
  new THREE.MeshBasicMaterial({ color: 0x457087 })
);
liveDot.rotation.x = Math.PI;   // tip points down toward workpiece
liveDot.visible = false;
scene.add(liveDot);

function resizeRenderer() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
resizeRenderer();
new ResizeObserver(resizeRenderer).observe(wrap);

(function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); })();

// ── State ─────────────────────────────────────────────────────────────────
let allPoints    = [];
let lineObjects  = [];
let currentJobId = null;
let liveTimer    = null;
let playTimer    = null;
let lastLiveTs   = null;
let isPlaying    = false;
let playFiltered = [];
let playIndex    = 0;
let playDrawn    = [];

const scrubberWrap = document.getElementById('tp-scrubber-wrap');
const scrubber     = document.getElementById('tp-scrubber');

function setScrubPos(idx, total) {
  const pct = total > 1 ? (idx / (total - 1)) * 100 : 0;
  scrubber.value = Math.round(pct * 10);
  scrubber.style.setProperty('--pct', pct.toFixed(2) + '%');
}
function showScrubber(show) { scrubberWrap.style.display = show ? 'block' : 'none'; }

const MAT_ARC   = new THREE.LineBasicMaterial({ color: 0x3fb950 });
const MAT_RAPID = new THREE.LineBasicMaterial({ color: 0x2a4a6a });

// KUKA world → Three.js (Y-up): kuka Z is height → three Y
function k2t(x, y, z) { return [x, z, -y]; }

// ── Scene management ──────────────────────────────────────────────────────
function clearLines() {
  for (const l of lineObjects) { scene.remove(l); l.geometry.dispose(); }
  lineObjects = [];
}

function buildLines(pts) {
  clearLines();
  if (!pts.length) return;
  let segs = [], cur = { arc: pts[0].arc_on, pts: [pts[0]] };
  for (let i = 1; i < pts.length; i++) {
    if (pts[i].arc_on === cur.arc) { cur.pts.push(pts[i]); }
    else { segs.push(cur); cur = { arc: pts[i].arc_on, pts: [pts[i]] }; }
  }
  segs.push(cur);
  for (const seg of segs) {
    const v = new Float32Array(seg.pts.length * 3);
    seg.pts.forEach((p, i) => { const [x,y,z] = k2t(p.x,p.y,p.z); v[i*3]=x; v[i*3+1]=y; v[i*3+2]=z; });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(v, 3));
    const line = new THREE.Line(geo, seg.arc ? MAT_ARC : MAT_RAPID);
    scene.add(line); lineObjects.push(line);
  }
}

function fitCamera(pts) {
  if (!pts.length) return;
  const box = new THREE.Box3();
  pts.forEach(p => { const [x,y,z] = k2t(p.x,p.y,p.z); box.expandByPoint(new THREE.Vector3(x,y,z)); });
  const center = new THREE.Vector3(); box.getCenter(center);
  const size = box.getSize(new THREE.Vector3());
  const d = Math.max(size.x, size.y, size.z, 200) * 1.5;
  controls.target.copy(center);
  camera.position.set(center.x + d, center.y + d * 0.6, center.z + d);
  camera.lookAt(center); controls.update();
}

// ── Filtering ─────────────────────────────────────────────────────────────
function filterPoints(pts) {
  const toolVal = document.getElementById('tp-tool').value;
  const arcOnly = document.getElementById('tp-arc-only').checked;
  return pts.filter(p => {
    if (arcOnly && !p.arc_on) return false;
    if (toolVal !== '' && String(p.tool_num) !== toolVal) return false;
    return true;
  });
}

function updateStats() {
  const f = filterPoints(allPoints);
  document.getElementById('tp-stats').textContent =
    allPoints.length ? `${f.length.toLocaleString()} pts shown / ${allPoints.length.toLocaleString()} total` : '';
}

function populateToolSelect(pts) {
  const tools = [...new Set(pts.map(p => p.tool_num).filter(t => t != null))].sort((a,b)=>a-b);
  const sel = document.getElementById('tp-tool');
  const cur = sel.value;
  sel.innerHTML = '<option value="">All Tools</option>';
  tools.forEach(t => { const o = document.createElement('option'); o.value=t; o.textContent=`Tool #${t}`; sel.appendChild(o); });
  if (cur) sel.value = cur;
}

// ── Jobs API ──────────────────────────────────────────────────────────────
async function loadJobs(robotId) {
  const jobs = await fetch(`/api/jobs?robot_id=${encodeURIComponent(robotId)}`).then(r=>r.json()).catch(()=>[]);
  const sel = document.getElementById('tp-job');
  sel.innerHTML = '<option value="">&#8212; select job &#8212;</option>';
  jobs.forEach(j => {
    const o = document.createElement('option');
    o.value = j.job_id;
    const d = new Date(j.started_at).toLocaleString();
    const pts = j.total_points.toLocaleString();
    const prog = j.program_name || '(unknown)';
    o.textContent = `${j.status==='active'?'🟢 ':'⚪ '}${prog} — ${d} (${pts} pts)`;
    sel.appendChild(o);
  });
  if (getMode() === 'live') {
    const active = jobs.find(j => j.status === 'active');
    if (active) { sel.value = active.job_id; await selectJob(active.job_id, false); }
  }
}

async function fetchToolpath(jobId, since) {
  const url = since ? `/api/toolpath/${jobId}/live?since=${encodeURIComponent(since)}`
                    : `/api/toolpath/${jobId}`;
  const res = await fetch(url).catch(()=>null);
  return (res && res.ok) ? res.json() : [];
}

async function selectJob(jobId, fit = true) {
  stopLive(); stopPlayback();
  currentJobId = jobId; allPoints = []; clearLines();
  playFiltered = []; playIndex = 0; playDrawn = [];
  setScrubPos(0, 1);
  overlay.textContent = 'Loading…'; overlay.style.display = 'flex';
  if (!jobId) { overlay.textContent = 'Select a robot and job to begin'; showScrubber(false); return; }

  const pts = await fetchToolpath(jobId);
  allPoints = pts; populateToolSelect(pts);
  buildLines(filterPoints(pts));
  if (fit && pts.length) fitCamera(pts);
  updateStats();
  overlay.style.display = 'none';

  if (getMode() === 'live') {
    showScrubber(false);
    lastLiveTs = pts.length ? pts.at(-1).ts : new Date(0).toISOString();
    startLive(jobId);
  } else {
    playFiltered = filterPoints(pts);
    showScrubber(playFiltered.length > 0);
  }
}

// ── Live polling ──────────────────────────────────────────────────────────
function setLiveDot(pt) {
  const tcpEnabled = document.getElementById('tp-tcp').checked;
  if (!pt || !tcpEnabled) { liveDot.visible = false; return; }
  const [x, y, z] = k2t(pt.x, pt.y, pt.z);
  liveDot.position.set(x, y + 20, z);  // tip is 20mm below center; raise so tip = TCP point
  liveDot.visible = true;
}

function startLive(jobId) {
  liveDot.visible = false;
  liveTimer = setInterval(async () => {
    if (!currentJobId) return;
    const newPts = await fetchToolpath(jobId, lastLiveTs);
    if (newPts.length) {
      lastLiveTs = newPts.at(-1).ts;
      newPts.forEach(p => allPoints.push(p));
      buildLines(filterPoints(allPoints));
      setLiveDot(filterPoints(allPoints).at(-1) ?? null);
      updateStats();
    }
  }, 1000);
}

function stopLive() {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  liveDot.visible = false;
}

// ── Playback ──────────────────────────────────────────────────────────────
function startPlayback() {
  if (!allPoints.length || isPlaying) return;
  playFiltered = filterPoints(allPoints);
  if (!playFiltered.length) return;
  if (playIndex >= playFiltered.length) { playIndex = 0; playDrawn = []; }
  isPlaying = true;
  document.getElementById('tp-play').textContent = '⏸ Pause';
  const speed = parseFloat(document.getElementById('tp-speed').value) || 1;
  function step() {
    if (!isPlaying || playIndex >= playFiltered.length) {
      isPlaying = false;
      document.getElementById('tp-play').textContent = '▶ Play';
      return;
    }
    playDrawn.push(playFiltered[playIndex]);
    buildLines(playDrawn);
    setScrubPos(playIndex, playFiltered.length);
    const dt = playFiltered[playIndex + 1]
      ? (new Date(playFiltered[playIndex + 1].ts) - new Date(playFiltered[playIndex].ts)) / speed : 0;
    playTimer = setTimeout(step, Math.min(Math.max(dt, 4), 1000));
    playIndex++;
  }
  step();
}

function stopPlayback() {
  isPlaying = false;
  if (playTimer) { clearTimeout(playTimer); playTimer = null; }
  document.getElementById('tp-play').textContent = '▶ Play';
}

function seekTo(idx) {
  playIndex = Math.max(0, Math.min(idx, playFiltered.length - 1));
  playDrawn = playFiltered.slice(0, playIndex + 1);
  buildLines(playDrawn);
  setScrubPos(playIndex, playFiltered.length);
}

function getMode() { return document.querySelector('input[name="tp-mode"]:checked')?.value ?? 'live'; }

// ── Control wiring ────────────────────────────────────────────────────────
document.getElementById('tp-robot').addEventListener('change', e => { stopLive(); stopPlayback(); loadJobs(e.target.value); });
document.getElementById('tp-job').addEventListener('change', e => { if (e.target.value) selectJob(e.target.value); });
document.querySelectorAll('input[name="tp-mode"]').forEach(r => r.addEventListener('change', () => {
  stopLive(); stopPlayback();
  showScrubber(r.value === 'playback' && !!currentJobId);
  if (currentJobId) selectJob(currentJobId, false);
}));
function applyFilter() {
  if (getMode() === 'playback' && allPoints.length) {
    playFiltered = filterPoints(allPoints);
    playIndex = Math.min(playIndex, Math.max(0, playFiltered.length - 1));
    playDrawn = playFiltered.slice(0, playIndex + 1);
    buildLines(playDrawn);
    setScrubPos(playIndex, playFiltered.length);
    showScrubber(playFiltered.length > 0);
  } else {
    buildLines(filterPoints(allPoints));
  }
  updateStats();
}
document.getElementById('tp-tool').addEventListener('change', applyFilter);
document.getElementById('tp-arc-only').addEventListener('change', applyFilter);
document.getElementById('tp-grid').addEventListener('change', e => { grid.visible = e.target.checked; });
document.getElementById('tp-axes').addEventListener('change', e => { axes.visible = e.target.checked; });
document.getElementById('tp-tcp').addEventListener('change', e => { if (!e.target.checked) liveDot.visible = false; });
document.getElementById('tp-play').addEventListener('click', () => {
  if (getMode() !== 'playback') return;
  if (isPlaying) { stopPlayback(); } else startPlayback();
});

// ── Scrubber ──────────────────────────────────────────────────────────────
let scrubWasPlaying = false;
scrubber.addEventListener('pointerdown', () => {
  scrubWasPlaying = isPlaying;
  if (isPlaying) { clearTimeout(playTimer); playTimer = null; isPlaying = false; }
});
scrubber.addEventListener('pointerup', () => {
  if (scrubWasPlaying) startPlayback();
});
scrubber.addEventListener('input', () => {
  if (!playFiltered.length) {
    playFiltered = filterPoints(allPoints);
    playDrawn = [];
  }
  if (!playFiltered.length) return;
  const idx = Math.round((scrubber.valueAsNumber / 1000) * (playFiltered.length - 1));
  seekTo(idx);
});
document.getElementById('tp-reset').addEventListener('click', () => {
  const pts = filterPoints(allPoints); fitCamera(pts.length ? pts : allPoints);
});
document.getElementById('tp-export-csv').addEventListener('click', () => {
  if (!currentJobId) return;
  const a = document.createElement('a'); a.href = `/api/toolpath/${currentJobId}/export.csv`; a.download = ''; a.click();
});
const tpModal = document.getElementById('tp-confirm-modal');
document.getElementById('tp-delete-job').addEventListener('click', () => {
  if (!currentJobId) return;
  tpModal.style.display = 'flex';
});
document.getElementById('tp-confirm-no').addEventListener('click', () => { tpModal.style.display = 'none'; });
document.getElementById('tp-confirm-yes').addEventListener('click', async () => {
  tpModal.style.display = 'none';
  if (!currentJobId) return;
  await fetch(`/api/jobs/${currentJobId}`, { method: 'DELETE' });
  currentJobId = null; allPoints = []; clearLines();
  overlay.textContent = 'Job deleted.'; overlay.style.display = 'flex';
  document.getElementById('tp-stats').textContent = '';
  const robot = document.getElementById('tp-robot').value;
  await loadJobs(robot);
  setTimeout(() => { if (overlay.textContent === 'Job deleted.') overlay.textContent = 'Select a robot and job to begin'; }, 2500);
});
tpModal.addEventListener('click', e => { if (e.target === e.currentTarget) tpModal.style.display = 'none'; });

// Initial load
loadJobs(document.getElementById('tp-robot').value);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    return web.Response(
        text=_HTML,
        content_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def health(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    try:
        rows = await pool.fetch("""
            SELECT device_id, MAX(ts) AS latest
            FROM radian_os.telemetry
            WHERE ts > NOW() - INTERVAL '60 seconds'
            GROUP BY device_id
        """)
        devices = {r["device_id"]: r["latest"].isoformat() for r in rows}
        status = "ok" if len(devices) >= 4 else "degraded"
        return web.Response(
            text=json.dumps({"status": status, "devices": devices}),
            content_type="application/json",
        )
    except Exception as exc:
        return web.Response(
            text=json.dumps({"status": "error", "error": str(exc)}),
            content_type="application/json",
            status=500,
        )


async def events(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })
    await resp.prepare(request)

    pool: asyncpg.Pool = request.app["pool"]

    # Latest snapshot per device (excluding system log records)
    seed = await pool.fetch("""
        SELECT DISTINCT ON (device_id)
            ts, device_id, source, changed_key, values::text AS vj
        FROM radian_os.telemetry
        WHERE device_id != 'system'
        ORDER BY device_id, ts DESC
    """)
    # Last 50 system log entries, oldest-first so browser prepend puts newest on top
    seed_logs = await pool.fetch("""
        SELECT ts, device_id, source, changed_key, vj
        FROM (
            SELECT ts, device_id, source, changed_key, values::text AS vj
            FROM radian_os.telemetry
            WHERE device_id = 'system'
            ORDER BY ts DESC
            LIMIT 50
        ) sub
        ORDER BY ts ASC
    """)
    for row in list(seed) + list(seed_logs):
        payload = json.dumps({
            "ts": row["ts"].isoformat(),
            "device_id": row["device_id"],
            "source": row["source"],
            "changed_key": row["changed_key"],
            "values": json.loads(row["vj"]),
        })
        await resp.write(f"data: {payload}\n\n".encode())

    last_ts = datetime.now(timezone.utc)
    last_keepalive = datetime.now(timezone.utc)

    try:
        while True:
            await asyncio.sleep(0.25)
            rows = await pool.fetch("""
                SELECT ts, device_id, source, changed_key, values::text AS vj
                FROM radian_os.telemetry
                WHERE ts > $1
                ORDER BY ts ASC
                LIMIT 200
            """, last_ts)

            if rows:
                last_ts = rows[-1]["ts"]
                for row in rows:
                    payload = json.dumps({
                        "ts": row["ts"].isoformat(),
                        "device_id": row["device_id"],
                        "source": row["source"],
                        "changed_key": row["changed_key"],
                        "values": json.loads(row["vj"]),
                    })
                    await resp.write(f"data: {payload}\n\n".encode())
            else:
                # Keepalive comment every 30s — prevents firewall/browser idle timeout
                now = datetime.now(timezone.utc)
                if (now - last_keepalive).total_seconds() >= 30:
                    await resp.write(b": keepalive\n\n")
                    last_keepalive = now
    except (ConnectionResetError, asyncio.CancelledError):
        pass

    return resp


# ---------------------------------------------------------------------------
# Toolpath / Jobs API handlers
# ---------------------------------------------------------------------------

async def api_jobs(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    robot_id = request.rel_url.query.get("robot_id")
    try:
        if robot_id:
            rows = await pool.fetch("""
                SELECT job_id, robot_id, program_name, started_at, last_active_at,
                       resumed_count, status, total_points
                FROM radian_os.print_jobs
                WHERE robot_id = $1
                ORDER BY started_at DESC LIMIT 200
            """, robot_id)
        else:
            rows = await pool.fetch("""
                SELECT job_id, robot_id, program_name, started_at, last_active_at,
                       resumed_count, status, total_points
                FROM radian_os.print_jobs
                ORDER BY started_at DESC LIMIT 200
            """)
        data = [
            {
                "job_id": r["job_id"],
                "robot_id": r["robot_id"],
                "program_name": r["program_name"],
                "started_at": r["started_at"].isoformat(),
                "last_active_at": r["last_active_at"].isoformat(),
                "resumed_count": r["resumed_count"],
                "status": r["status"],
                "total_points": r["total_points"],
            }
            for r in rows
        ]
        return web.Response(text=json.dumps(data), content_type="application/json")
    except Exception as exc:
        return web.Response(text=json.dumps({"error": str(exc)}),
                            content_type="application/json", status=500)


async def api_jobs_delete(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    try:
        job_id = int(request.match_info["job_id"])
    except (KeyError, ValueError):
        return web.Response(text=json.dumps({"error": "invalid job_id"}),
                            content_type="application/json", status=400)
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM radian_os.toolpath_points WHERE job_id = $1", job_id)
            await conn.execute(
                "DELETE FROM radian_os.toolpath_points WHERE job_id = $1", job_id)
            await conn.execute(
                "DELETE FROM radian_os.print_jobs WHERE job_id = $1", job_id)
        return web.Response(
            text=json.dumps({"deleted": True, "points_removed": int(n or 0)}),
            content_type="application/json")
    except Exception as exc:
        return web.Response(text=json.dumps({"error": str(exc)}),
                            content_type="application/json", status=500)


async def api_toolpath(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    try:
        job_id = int(request.match_info["job_id"])
    except (KeyError, ValueError):
        return web.Response(text=json.dumps({"error": "invalid job_id"}),
                            content_type="application/json", status=400)

    q = request.rel_url.query
    limit = min(int(q.get("limit", 50000)), 200000)

    wheres = ["job_id = $1"]
    params: list[Any] = [job_id]

    if "arc_on" in q:
        params.append(q["arc_on"].lower() in ("true", "1", "yes"))
        wheres.append(f"arc_on = ${len(params)}")
    if "tool_num" in q:
        try:
            params.append(int(q["tool_num"]))
            wheres.append(f"tool_num = ${len(params)}")
        except ValueError:
            pass
    if "since" in q:
        try:
            since_dt = datetime.fromisoformat(q["since"])
        except ValueError:
            since_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        params.append(since_dt)
        wheres.append(f"ts > ${len(params)}")

    params.append(limit)
    sql = f"""
        SELECT ts, tcp_x AS x, tcp_y AS y, tcp_z AS z,
               tool_num, arc_on, seam_number AS seam, layer,
               in_motion, on_path, speed_override
        FROM radian_os.toolpath_points
        WHERE {' AND '.join(wheres)}
        ORDER BY ts ASC
        LIMIT ${len(params)}
    """
    try:
        rows = await pool.fetch(sql, *params)
        data = [
            {
                "ts": r["ts"].isoformat(),
                "x": r["x"], "y": r["y"], "z": r["z"],
                "tool_num": r["tool_num"],
                "arc_on": r["arc_on"],
                "seam": r["seam"],
                "layer": r["layer"],
                "in_motion": r["in_motion"],
                "on_path": r["on_path"],
                "speed_override": r["speed_override"],
            }
            for r in rows
        ]
        return web.Response(text=json.dumps(data), content_type="application/json")
    except Exception as exc:
        return web.Response(text=json.dumps({"error": str(exc)}),
                            content_type="application/json", status=500)


async def api_toolpath_live(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    try:
        job_id = int(request.match_info["job_id"])
    except (KeyError, ValueError):
        return web.Response(text=json.dumps({"error": "invalid job_id"}),
                            content_type="application/json", status=400)

    since_str = request.rel_url.query.get("since", "1970-01-01")
    try:
        since_dt = datetime.fromisoformat(since_str)
    except ValueError:
        since_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        rows = await pool.fetch("""
            SELECT ts, tcp_x AS x, tcp_y AS y, tcp_z AS z,
                   tool_num, arc_on, seam_number AS seam, layer,
                   in_motion, on_path, speed_override
            FROM radian_os.toolpath_points
            WHERE job_id = $1 AND ts > $2
            ORDER BY ts ASC LIMIT 2000
        """, job_id, since_dt)
        data = [
            {
                "ts": r["ts"].isoformat(),
                "x": r["x"], "y": r["y"], "z": r["z"],
                "tool_num": r["tool_num"],
                "arc_on": r["arc_on"],
                "seam": r["seam"],
                "layer": r["layer"],
                "in_motion": r["in_motion"],
                "on_path": r["on_path"],
                "speed_override": r["speed_override"],
            }
            for r in rows
        ]
        return web.Response(text=json.dumps(data), content_type="application/json")
    except Exception as exc:
        return web.Response(text=json.dumps({"error": str(exc)}),
                            content_type="application/json", status=500)


async def api_toolpath_csv(request: web.Request) -> web.StreamResponse:
    pool: asyncpg.Pool = request.app["pool"]
    try:
        job_id = int(request.match_info["job_id"])
    except (KeyError, ValueError):
        return web.Response(text="invalid job_id", status=400)

    job_row = await pool.fetchrow(
        "SELECT program_name FROM radian_os.print_jobs WHERE job_id = $1", job_id)
    prog = (job_row["program_name"] if job_row else "unknown")
    safe_prog = "".join(c if c.isalnum() or c in "-_." else "_" for c in prog)
    filename = f"job_{job_id}_{safe_prog}.csv"

    resp = web.StreamResponse(headers={
        "Content-Type": "text/csv",
        "Content-Disposition": f'attachment; filename="{filename}"',
    })
    await resp.prepare(request)
    header = "ts,tcp_x_mm,tcp_y_mm,tcp_z_mm,tool_num,arc_on,seam_number,layer,in_motion,on_path,speed_override_pct,program_state\r\n"
    await resp.write(header.encode())

    def _s(v: Any) -> str:
        return "" if v is None else str(v)

    async with pool.acquire() as conn:
        async for row in conn.cursor("""
            SELECT ts, tcp_x, tcp_y, tcp_z, tool_num, arc_on, seam_number, layer,
                   in_motion, on_path, speed_override, program_state
            FROM radian_os.toolpath_points
            WHERE job_id = $1
            ORDER BY ts ASC
        """, job_id):
            line = (
                f"{row['ts'].isoformat()},{_s(row['tcp_x'])},{_s(row['tcp_y'])},{_s(row['tcp_z'])},"
                f"{_s(row['tool_num'])},{_s(row['arc_on'])},{_s(row['seam_number'])},{_s(row['layer'])},"
                f"{_s(row['in_motion'])},{_s(row['on_path'])},{_s(row['speed_override'])},{_s(row['program_state'])}\r\n"
            )
            await resp.write(line.encode())
    return resp


# ---------------------------------------------------------------------------
# App factory + entrypoint
# ---------------------------------------------------------------------------

async def create_app(dsn: str) -> web.Application:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    app = web.Application()
    app["pool"] = pool
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/events", events)
    app.router.add_get("/api/jobs", api_jobs)
    app.router.add_delete("/api/jobs/{job_id}", api_jobs_delete)
    app.router.add_get("/api/toolpath/{job_id}/live", api_toolpath_live)
    app.router.add_get("/api/toolpath/{job_id}/export.csv", api_toolpath_csv)
    app.router.add_get("/api/toolpath/{job_id}", api_toolpath)

    async def _close(a: web.Application) -> None:
        await a["pool"].close()
    app.on_shutdown.append(_close)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Radian OS 2.0 live dashboard")
    parser.add_argument("--config", default="config/collectors.local.yaml")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    dsn = cfg["storage"]["dsn"]

    async def _run() -> None:
        app = await create_app(dsn)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", args.port)
        await site.start()
        print(f"\n  Radian OS 2.0 dashboard ->  http://localhost:{args.port}\n")
        await asyncio.Event().wait()  # run forever

    asyncio.run(_run())


if __name__ == "__main__":
    main()

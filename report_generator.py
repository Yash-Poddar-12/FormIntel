"""CSV + JSON + HTML report generation for smart_form_tester."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Template


class ReportGenerator:
    """Generate CSV, JSON and styled HTML reports from test results."""

    def generate(
        self,
        results: list[dict],
        output_dir: str = "reports",
        timestamp: str | None = None,
    ) -> None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        if not timestamp:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        csv_path = output_path / f"report_{timestamp}.csv"
        json_path = output_path / f"report_{timestamp}.json"
        html_path = output_path / f"report_{timestamp}.html"

        self._generate_csv(results, csv_path)
        self._generate_json(results, json_path, timestamp)
        self._generate_html_from_json(json_path, html_path)

        print(f"[Report] CSV  → {csv_path}")
        print(f"[Report] JSON → {json_path}")
        print(f"[Report] HTML → {html_path}")

    def _generate_csv(self, results: list[dict], csv_path: Path) -> None:
        with csv_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow([
                "Test Number",
                "Test Name",
                "Changed Field",
                "Changed Value",
                "Status",
                "Pass Reason",
                "Page Errors",
                "Validation Messages",
                "Final URL",
                "Page Number",
            ])
            for i, row in enumerate(results, start=1):
                writer.writerow([
                    i,
                    row.get("test_name", ""),
                    row.get("changed_field") or "—",
                    self._to_json_string(row.get("changed_value")),
                    row.get("status", ""),
                    row.get("pass_reason", ""),
                    " | ".join(row.get("page_errors") or []),
                    self._to_json_string(row.get("validation_messages", {})),
                    row.get("url", ""),
                    row.get("page_number", 1),
                ])

    def _generate_json(self, results: list[dict], json_path: Path, timestamp: str) -> None:
        total = len(results)
        pass_count = sum(1 for r in results if str(r.get("status", "")).upper() == "PASS")
        fail_count = sum(1 for r in results if str(r.get("status", "")).upper() == "FAIL")
        error_count = total - pass_count - fail_count

        payload = {
            "meta": {
                "generated_at": timestamp,
                "total_tests": total,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "error_count": error_count,
                "pass_rate_percent": round(pass_count / total * 100, 2) if total else 0,
            },
            "results": [],
        }

        for i, row in enumerate(results, start=1):
            payload["results"].append({
                "test_number": i,
                "test_name": row.get("test_name", ""),
                "changed_field": row.get("changed_field") or None,
                "changed_value": row.get("changed_value"),
                "status": str(row.get("status", "")).upper(),
                "pass_reason": row.get("pass_reason", ""),
                "page_errors": row.get("page_errors") or [],
                "validation_messages": row.get("validation_messages") or {},
                "all_field_values": row.get("all_values") or {},
                "final_url": row.get("url", ""),
                "page_number": row.get("page_number", 1),
            })

        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _generate_html_from_json(self, json_path: Path, html_path: Path) -> None:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        meta = raw.get("meta", {})
        results = raw.get("results", [])

        rows = []
        for row in results:
            rows.append({
                "test_number": row.get("test_number"),
                "test_name": row.get("test_name", ""),
                "changed_field": row.get("changed_field") or "—",
                "changed_value": self._to_json_string(row.get("changed_value")),
                "status": str(row.get("status", "")).upper(),
                "pass_reason": row.get("pass_reason", ""),
                "errors": " | ".join(row.get("page_errors") or []) or "—",
                "validation": self._to_json_string(row.get("validation_messages") or {}),
                "url": row.get("final_url", ""),
                "page_number": row.get("page_number", 1),
                "all_values_json": json.dumps(
                    row.get("all_field_values") or {}, indent=2, ensure_ascii=False
                ),
            })

        html = Template(self._html_template()).render(
            generated_at=meta.get("generated_at", ""),
            total=meta.get("total_tests", 0),
            pass_count=meta.get("pass_count", 0),
            fail_count=meta.get("fail_count", 0),
            error_count=meta.get("error_count", 0),
            pass_rate=meta.get("pass_rate_percent", 0),
            rows=rows,
            json_filename=json_path.name,
        )
        html_path.write_text(html, encoding="utf-8")

    def regenerate_html_from_json(self, json_file: str, output_dir: str = ".") -> None:
        json_path = Path(json_file)
        if not json_path.exists():
            print(f"[Report] JSON file not found: {json_file}")
            return
        html_path = Path(output_dir) / json_path.name.replace(".json", ".html")
        self._generate_html_from_json(json_path, html_path)
        print(f"[Report] HTML regenerated → {html_path}")

    @staticmethod
    def _to_json_string(value: Any, pretty: bool = False) -> str:
        try:
            if pretty:
                return json.dumps(value, indent=2, ensure_ascii=False)
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    @staticmethod
    def _html_template() -> str:
        return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Smart Form Tester Report</title>
  <style>
    :root{--bg:#0f0f0f;--panel:#171717;--text:#e5e5e5;--muted:#a3a3a3;
          --green:#22c55e;--red:#ef4444;--yellow:#f59e0b;--border:#262626;}
    *{box-sizing:border-box;margin:0;padding:0;}
    body{background:var(--bg);color:var(--text);
         font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
    .wrap{max-width:1380px;margin:28px auto;padding:0 16px 40px;}
    .header{background:var(--panel);border:1px solid var(--border);
            border-radius:14px;padding:20px 24px;margin-bottom:16px;}
    .header h1{font-size:22px;font-weight:700;}
    .header .sub{margin-top:6px;color:var(--muted);font-size:13px;}
    .cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px;}
    .card{background:var(--panel);border:1px solid var(--border);
          border-radius:12px;padding:16px 14px;}
    .card .lbl{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;}
    .card .val{font-size:26px;font-weight:700;margin-top:8px;line-height:1;}
    .card.pass .val{color:var(--green);}
    .card.fail .val{color:var(--red);}
    .card.warn .val{color:var(--yellow);}
    .toolbar{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap;align-items:center;}
    .toolbar input{flex:1;min-width:200px;background:#111;border:1px solid var(--border);
                   border-radius:8px;padding:8px 12px;color:var(--text);font-size:13px;}
    .toolbar input:focus{outline:none;border-color:#555;}
    .filter-btn{padding:7px 14px;border-radius:8px;border:1px solid var(--border);
                background:#111;color:var(--muted);font-size:12px;cursor:pointer;}
    .filter-btn.active{border-color:#555;color:var(--text);background:#1e1e1e;}
    .tbl-wrap{background:var(--panel);border:1px solid var(--border);
              border-radius:14px;overflow:hidden;}
    table{width:100%;border-collapse:collapse;}
    thead th{text-align:left;font-size:11px;letter-spacing:.05em;text-transform:uppercase;
             color:var(--muted);background:#111;padding:11px 10px;
             border-bottom:1px solid var(--border);}
    tbody tr.main-row{border-bottom:1px solid var(--border);cursor:pointer;transition:background .12s;}
    tbody tr.main-row:hover{background:#1c1c1c;}
    tbody tr.main-row.pass{border-left:4px solid var(--green);}
    tbody tr.main-row.fail{border-left:4px solid var(--red);}
    tbody tr.main-row.error{border-left:4px solid var(--yellow);}
    tbody td{padding:10px;font-size:13px;vertical-align:top;word-break:break-word;}
    .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;}
    .badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;
           font-weight:700;letter-spacing:.04em;text-transform:uppercase;}
    .badge.PASS{background:rgba(34,197,94,.15);color:#86efac;border:1px solid rgba(34,197,94,.35);}
    .badge.FAIL{background:rgba(239,68,68,.15);color:#fca5a5;border:1px solid rgba(239,68,68,.35);}
    .badge.ERROR{background:rgba(245,158,11,.15);color:#fcd34d;border:1px solid rgba(245,158,11,.35);}
    .reason{font-size:11px;color:var(--muted);margin-top:3px;}
    tr.detail-row{display:none;background:#111;}
    tr.detail-row.open{display:table-row;}
    .detail-inner{padding:14px 16px 18px;display:grid;grid-template-columns:1fr 1fr;gap:14px;}
    .detail-section h4{color:var(--muted);font-size:11px;text-transform:uppercase;
                       letter-spacing:.05em;margin-bottom:8px;}
    pre{background:#0b0b0b;border:1px solid var(--border);border-radius:8px;padding:12px;
        color:#d4d4d4;overflow:auto;max-height:260px;font-size:12px;line-height:1.5;}
    tr.main-row.hidden-row{display:none;}
  </style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>Smart Form Tester — Test Report</h1>
    <div class="sub">Generated: {{ generated_at }} &nbsp;·&nbsp;
      Source: <a href="{{ json_filename }}" style="color:#60a5fa;">{{ json_filename }}</a>
    </div>
  </div>

  <div class="cards">
    <div class="card"><div class="lbl">Total Tests</div><div class="val">{{ total }}</div></div>
    <div class="card pass"><div class="lbl">PASS</div><div class="val">{{ pass_count }}</div></div>
    <div class="card fail"><div class="lbl">FAIL</div><div class="val">{{ fail_count }}</div></div>
    <div class="card warn"><div class="lbl">ERROR</div><div class="val">{{ error_count }}</div></div>
    <div class="card"><div class="lbl">Pass Rate</div><div class="val">{{ pass_rate }}%</div></div>
  </div>

  <div class="toolbar">
    <input id="searchBox" type="text" placeholder="Search test name, field, value…"/>
    <button class="filter-btn active" data-filter="ALL">All</button>
    <button class="filter-btn" data-filter="PASS">PASS</button>
    <button class="filter-btn" data-filter="FAIL">FAIL</button>
    <button class="filter-btn" data-filter="ERROR">ERROR</button>
  </div>

  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Test Name</th><th>Changed Field</th>
          <th>Changed Value</th><th>Status</th><th>Errors</th><th>URL</th>
        </tr>
      </thead>
      <tbody>
        {% for row in rows %}
        <tr class="main-row {{ row.status | lower }}"
            data-status="{{ row.status }}"
            data-search="{{ row.test_name }} {{ row.changed_field }} {{ row.changed_value }}"
            data-detail="detail-{{ row.test_number }}">
          <td>{{ row.test_number }}</td>
          <td>{{ row.test_name }}</td>
          <td>{{ row.changed_field }}</td>
          <td class="mono">{{ row.changed_value }}</td>
          <td>
            <span class="badge {{ row.status }}">{{ row.status }}</span>
            {% if row.pass_reason %}
              <div class="reason">{{ row.pass_reason }}</div>
            {% endif %}
          </td>
          <td style="max-width:280px;font-size:12px;">{{ row.errors }}</td>
          <td class="mono" style="font-size:11px;">{{ row.url }}</td>
        </tr>
        <tr id="detail-{{ row.test_number }}" class="detail-row">
          <td colspan="7">
            <div class="detail-inner">
              <div class="detail-section">
                <h4>All Field Values Used</h4>
                <pre class="mono">{{ row.all_values_json }}</pre>
              </div>
              <div class="detail-section">
                <h4>Browser Validation Messages</h4>
                <pre class="mono">{{ row.validation }}</pre>
                <h4 style="margin-top:14px;">Page Errors Detected</h4>
                <pre class="mono">{{ row.errors }}</pre>
              </div>
            </div>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
<script>
(function(){
  document.querySelectorAll("tr.main-row").forEach(row=>{
    row.addEventListener("click",()=>{
      const d=document.getElementById(row.getAttribute("data-detail"));
      if(d) d.classList.toggle("open");
    });
  });
  let activeFilter="ALL";
  document.querySelectorAll(".filter-btn").forEach(btn=>{
    btn.addEventListener("click",()=>{
      document.querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));
      btn.classList.add("active");
      activeFilter=btn.getAttribute("data-filter");
      applyFilters();
    });
  });
  document.getElementById("searchBox").addEventListener("input",applyFilters);
  function applyFilters(){
    const q=document.getElementById("searchBox").value.toLowerCase();
    document.querySelectorAll("tr.main-row").forEach(row=>{
      const ms=activeFilter==="ALL"||row.getAttribute("data-status")===activeFilter;
      const mq=!q||(row.getAttribute("data-search")||"").toLowerCase().includes(q);
      row.classList.toggle("hidden-row",!(ms&&mq));
      if(!(ms&&mq)){
        const d=document.getElementById(row.getAttribute("data-detail"));
        if(d) d.classList.remove("open");
      }
    });
  }
})();
</script>
</body>
</html>
"""
import csv
import io
import json
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from main import (
    configure_console,
    deduplicate_verified,
    fixed_category,
    lead_contact_priority,
    verify_candidate,
)
from scraper.search import deduplicate_businesses, discover_businesses

configure_console()


DB_PATH = Path("data") / "scraper_jobs.sqlite3"
CSV_COLUMNS = [
    "business_name",
    "category",
    "website",
    "phone",
    "address",
    "email",
]

@asynccontextmanager
async def lifespan(_app):
    del _app
    init_db()
    yield


app = FastAPI(title="Business Lead Scraper", lifespan=lifespan)
_jobs = {}
_jobs_lock = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                categories TEXT NOT NULL,
                locations TEXT NOT NULL,
                status TEXT NOT NULL,
                discovered INTEGER NOT NULL DEFAULT 0,
                verified INTEGER NOT NULL DEFAULT 0,
                rejected INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 0,
                partial INTEGER NOT NULL DEFAULT 0,
                phase TEXT NOT NULL DEFAULT 'Queued',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                business_name TEXT NOT NULL,
                category TEXT NOT NULL,
                website TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                email TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            CREATE TABLE IF NOT EXISTS diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "phase" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN phase TEXT NOT NULL DEFAULT 'Queued'"
            )


class JobRequest(BaseModel):
    categories: list[str] = Field(min_length=1)
    locations: list[str] = Field(min_length=1)


def update_job(job_id, **values):
    values["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with db() as connection:
        connection.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            [*values.values(), job_id],
        )


def add_diagnostic(job_id, message):
    with db() as connection:
        connection.execute(
            "INSERT INTO diagnostics(job_id, message, created_at) VALUES (?, ?, ?)",
            (job_id, message, utc_now()),
        )


def save_lead(job_id, lead):
    with db() as connection:
        connection.execute(
            """
            INSERT INTO leads
            (job_id, business_name, category, website, phone, address, email, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                lead.get("business_name", ""),
                lead.get("category", ""),
                lead.get("website", ""),
                lead.get("phone", ""),
                lead.get("address", ""),
                lead.get("email", ""),
                json.dumps({
                    "source": lead.get("source", ""),
                    "verification_status": lead.get("verification_status", ""),
                    "verification_reasons": lead.get("verification_reasons", []),
                    "evidence_confidence": lead.get("evidence_confidence", {}),
                }, ensure_ascii=False),
            ),
        )


def run_job(job_id, categories, locations):
    try:
        update_job(job_id, status="running", phase="Preparing sources")
        candidates = []
        for category_input in categories:
            category = fixed_category(category_input)
            for location in locations:
                with _jobs_lock:
                    if _jobs.get(job_id, {}).get("cancelled"):
                        update_job(job_id, status="cancelled", phase="Cancelled")
                        return
                update_job(
                    job_id,
                    phase=f"Scanning {category} in {location}",
                )
                found = discover_businesses(category, location, wanted=0)
                for candidate in found:
                    candidate["category"] = category
                    candidate["requested_category"] = category
                    candidate["requested_location"] = location
                candidates.extend(found)
                update_job(job_id, discovered=len(candidates))

        candidates = deduplicate_businesses(candidates)
        verified = []
        rejected = 0
        total_candidates = len(candidates)
        for index, candidate in enumerate(candidates, start=1):
            with _jobs_lock:
                if _jobs.get(job_id, {}).get("cancelled"):
                    update_job(job_id, status="cancelled", phase="Cancelled")
                    return
            update_job(
                job_id,
                phase=f"Verifying candidate {index} of {total_candidates}",
            )
            reasons = []
            lead = verify_candidate(
                candidate,
                fixed_category(candidate.get("category", "")),
                candidate.get("requested_location", candidate.get("search_location", "")),
                reasons,
            )
            if lead:
                verified.append(lead)
            else:
                rejected += 1
            update_job(job_id, verified=len(verified), rejected=rejected)

        verified = deduplicate_verified(verified)
        verified.sort(
            key=lambda lead: (
                lead_contact_priority(lead),
                lead.get("automation_score", 0),
            ),
            reverse=True,
        )
        complete = sum(
            bool(lead.get("website"))
            and bool(lead.get("phone"))
            and bool(lead.get("email"))
            for lead in verified
        )
        for lead in verified:
            save_lead(job_id, lead)
        update_job(
            job_id,
            status="completed",
            phase="Complete",
            verified=len(verified),
            complete=complete,
            partial=len(verified) - complete,
            rejected=rejected,
        )
    except Exception as error:
        add_diagnostic(job_id, f"{type(error).__name__}: {error}")
        update_job(job_id, status="failed", phase="Failed", error=str(error))


@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!doctype html>
    <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Atlas Leads | Business Intelligence</title>
    <link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Playfair+Display:wght@600;700&display=swap" rel="stylesheet">
    <style>
    :root{--ink:#101827;--navy:#16243a;--navy2:#0d1728;--gold:#c9a86a;--cream:#f6f3ed;--muted:#718096;--line:#e7e3db;--green:#22845b;--red:#c64b4b}
    *{box-sizing:border-box}body{margin:0;background:var(--cream);color:var(--ink);font:14px "DM Sans",sans-serif;overflow-x:hidden}.shell{animation:page-in .7s ease both}@keyframes page-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
    body:before,body:after{content:"";position:fixed;z-index:-1;border-radius:50%;filter:blur(2px);pointer-events:none}body:before{width:320px;height:320px;background:#d9c28c33;top:80px;right:-120px;animation:float 12s ease-in-out infinite}body:after{width:240px;height:240px;background:#b8c9d833;bottom:-80px;left:180px;animation:float 15s ease-in-out infinite reverse}@keyframes float{50%{transform:translate(-25px,18px) scale(1.08)}}
    .shell{display:grid;grid-template-columns:248px 1fr;min-height:100vh}.sidebar{background:var(--navy2);color:#fff;padding:28px 20px;position:relative}
    .brand{display:flex;align-items:center;gap:11px;margin:0 10px 62px}.brand-mark{width:34px;height:34px;border:1px solid var(--gold);border-radius:10px;display:grid;place-items:center;color:var(--gold);font:700 18px "Playfair Display"}
    .brand strong{font:700 16px "Playfair Display";letter-spacing:.5px}.brand small{display:block;color:#8d9aae;font-size:9px;letter-spacing:2px;text-transform:uppercase;margin-top:3px}.brand-mark{animation:mark-glow 3s ease-in-out infinite}@keyframes mark-glow{50%{box-shadow:0 0 18px #c9a86a55}}
    .nav-label{color:#718096;font-size:10px;text-transform:uppercase;letter-spacing:1.6px;margin:0 12px 12px}.nav-item{display:flex;gap:12px;align-items:center;color:#bdc7d5;padding:12px;border-radius:9px;margin:4px 0}.nav-item.active{background:#243653;color:#fff}.nav-icon{color:var(--gold);width:18px;text-align:center}
    .sidebar-footer{position:absolute;bottom:25px;left:30px;right:25px;color:#738198;font-size:11px;line-height:1.7}.sidebar-footer b{color:#aab5c5;font-weight:500}
    main{min-width:0}.topbar{height:76px;background:#fff;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;padding:0 42px}.eyebrow{color:var(--gold);font-size:10px;letter-spacing:1.6px;text-transform:uppercase;font-weight:700}.topbar h1{font:700 25px "Playfair Display";margin:4px 0 0}.secure{color:var(--muted);font-size:12px}.secure span{color:var(--green);margin-right:7px}
    .content{max-width:1320px;padding:36px 42px}.hero{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:25px}.hero h2{font:700 30px "Playfair Display";margin:0 0 8px}.hero p{color:var(--muted);margin:0}.badge{background:#e8f3ee;color:var(--green);border-radius:20px;padding:8px 13px;font-size:11px;font-weight:600}
    .panel{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 10px 30px #2634480b;transition:box-shadow .3s,transform .3s}.panel:hover{box-shadow:0 16px 38px #26344814}.search-panel{padding:25px;margin-bottom:22px}.panel-title{font-weight:700;margin:0 0 18px;font-size:15px}.form-grid{display:grid;grid-template-columns:1fr 1fr auto;gap:14px;align-items:end}.field label{display:block;color:#657285;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;margin:0 0 7px}.field input{width:100%;border:1px solid #d8dce2;border-radius:8px;padding:13px 14px;color:var(--ink);font:14px "DM Sans";outline:0;background:#fcfcfb;transition:border-color .25s,box-shadow .25s,transform .25s}.field input:focus{border-color:var(--gold);box-shadow:0 0 0 3px #c9a86a22;transform:translateY(-1px)}.primary{border:0;border-radius:8px;padding:13px 21px;background:var(--navy);color:#fff;font:600 13px "DM Sans";cursor:pointer;white-space:nowrap;transition:transform .25s,box-shadow .25s,background .25s}.primary:hover{background:#223656;transform:translateY(-2px);box-shadow:0 7px 18px #16243a33}.primary:active{transform:translateY(0)}.primary:disabled{opacity:.6;cursor:wait}
    .status-panel{padding:22px;display:none;margin-bottom:22px;animation:slide-down .45s ease both}@keyframes slide-down{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:none}}.status-head{display:flex;justify-content:space-between;align-items:center}.status-title{font-weight:700}.status-copy{color:var(--muted);font-size:12px}.progress{height:7px;background:#edf0f3;border-radius:10px;overflow:hidden;margin:17px 0}.progress i{display:block;height:100%;width:5%;background:linear-gradient(90deg,var(--gold),#e3c787);border-radius:10px;transition:width .4s;position:relative}.progress i:after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,#fff8,transparent);animation:shine 1.4s linear infinite}@keyframes shine{from{transform:translateX(-100%)}to{transform:translateX(100%)}}.metrics{display:flex;gap:32px}.metric b{font-size:21px;transition:color .25s}.metric span{display:block;color:var(--muted);font-size:11px;margin-top:2px}.actions{display:flex;gap:8px}.ghost{border:1px solid #d9dee5;background:#fff;color:var(--navy);border-radius:7px;padding:8px 12px;font:600 11px "DM Sans";cursor:pointer;transition:background .2s,transform .2s}.ghost:hover{background:#f6f3ed;transform:translateY(-1px)}.danger{color:var(--red);border-color:#edcccc}
    .activity{display:flex;align-items:center;gap:8px;color:#8a6b2f;font-size:11px;margin-top:9px}.pulse{width:7px;height:7px;background:var(--gold);border-radius:50%;box-shadow:0 0 0 0 #c9a86a66;animation:pulse 1.5s infinite}@keyframes pulse{70%{box-shadow:0 0 0 7px #c9a86a00}}
    .results-head{display:flex;justify-content:space-between;align-items:center;margin:30px 0 13px}.results-head h3{font:700 20px "Playfair Display";margin:0}.count{color:var(--muted);font-size:12px}.table-wrap{overflow:auto}.results-table{border-collapse:collapse;width:100%;min-width:850px}.results-table th{text-align:left;background:#faf9f6;color:#788394;font-size:10px;letter-spacing:1px;text-transform:uppercase;padding:13px 15px;border-bottom:1px solid var(--line)}    .results-table td{padding:15px;border-bottom:1px solid #f0eee9;vertical-align:top}.results-table tbody tr{animation:row-in .45s ease both}.results-table tbody tr:nth-child(2){animation-delay:.04s}.results-table tbody tr:nth-child(3){animation-delay:.08s}.results-table tbody tr:nth-child(4){animation-delay:.12s}@keyframes row-in{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}.results-table tr:hover td{background:#fdfcf9}.name{font-weight:700;color:var(--navy)}.sub{display:block;color:#9aa3af;font-size:11px;margin-top:4px}.link{color:#3b678f;text-decoration:none;max-width:180px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.link:hover{text-decoration:underline}.empty{text-align:center;padding:55px 20px;color:var(--muted)}.empty .empty-icon{font-size:28px;color:var(--gold);margin-bottom:10px;animation:twinkle 2.5s ease-in-out infinite}@keyframes twinkle{50%{transform:scale(1.18);opacity:.65}}.error{color:var(--red);background:#fff2f2;border:1px solid #f1d2d2;border-radius:8px;padding:12px;font-size:12px;animation:shake .35s ease}@keyframes shake{25%{transform:translateX(-4px)}75%{transform:translateX(4px)}}
    .suggestions{display:flex;gap:7px;flex-wrap:wrap;margin-top:13px}.suggestion{border:1px solid #e6dfd1;background:#fbf8f1;color:#8a6b2f;border-radius:20px;padding:6px 10px;font:11px "DM Sans";cursor:pointer;transition:all .2s}.suggestion:hover{background:#f1e6cf;transform:translateY(-1px)}
    @media(max-width:850px){.shell{display:block}.sidebar{display:none}.topbar{padding:0 20px}.content{padding:25px 18px}.form-grid{grid-template-columns:1fr}.hero{display:block}.badge{display:inline-block;margin-top:15px}.secure{display:none}}
    </style></head><body><div class="shell">
    <aside class="sidebar"><div class="brand"><div class="brand-mark">A</div><div><strong>ATLAS LEADS</strong><small>Intelligence suite</small></div></div>
    <div class="nav-label">Workspace</div><div class="nav-item active"><span class="nav-icon">⌕</span>Lead discovery</div><div class="nav-item"><span class="nav-icon">◇</span>Verified prospects</div><div class="nav-item"><span class="nav-icon">▱</span>Exports</div>
    <div class="sidebar-footer">Powered by public business data<br><b>OSM-first · Website verified</b></div></aside>
    <main><header class="topbar"><div><div class="eyebrow">Workspace / Discovery</div><h1>Business Intelligence</h1></div><div class="secure"><span>●</span>Local & private</div></header>
    <section class="content"><div class="hero"><div><h2>Discover your next opportunity.</h2><p>Find businesses with verified contact details, ranked by completeness.</p></div><div class="badge">✦ Free intelligence search</div></div>
    <div class="panel search-panel"><div class="panel-title">Start a new discovery</div><form id="form"><div class="form-grid"><div class="field"><label for="categories">Business categories</label><input id="categories" placeholder="e.g. toy store, cafe" required></div><div class="field"><label for="locations">Locations</label><input id="locations" placeholder="e.g. Chennai, Madurai" required></div><button class="primary" id="submit" type="submit">Begin discovery&nbsp; →</button></div><div class="suggestions"><button class="suggestion" type="button" data-category="toy store" data-location="Chennai">Toy stores · Chennai</button><button class="suggestion" type="button" data-category="cafe" data-location="Coimbatore">Cafes · Coimbatore</button><button class="suggestion" type="button" data-category="gym" data-location="Erode">Gyms · Erode</button></div></form></div>
    <div class="panel status-panel" id="statusPanel"><div class="status-head"><div><div class="status-title" id="statusTitle">Preparing discovery</div><div class="status-copy" id="statusCopy">Connecting to public sources…</div><div class="activity"><i class="pulse" id="pulse"></i><span id="activity">Waiting for the first source response</span></div></div><div class="actions"><button class="ghost danger" id="cancel" type="button">Cancel</button><button class="ghost" id="download" type="button" hidden>Download CSV</button></div></div><div class="progress"><i id="bar"></i></div><div class="metrics"><div class="metric"><b id="discovered">0</b><span>Discovered</span></div><div class="metric"><b id="verified">0</b><span>Verified</span></div><div class="metric"><b id="complete">0</b><span>Complete contacts</span></div><div class="metric"><b id="rejected">0</b><span>Filtered</span></div></div></div>
    <div class="results-head"><h3>Lead directory</h3><span class="count" id="count">No search run yet</span></div><div class="panel table-wrap" id="results"><div class="empty"><div class="empty-icon">✦</div><div>Your verified prospects will appear here.</div><div class="sub">Complete website, phone and email records are prioritized.</div></div></div>
    </section></main></div>
    <script>
    const $=id=>document.getElementById(id), form=$('form'), panel=$('statusPanel'), results=$('results'), submit=$('submit'), cancel=$('cancel'), download=$('download');
    let timer=null, jobId=null; const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
    const split=v=>v.split(',').map(x=>x.trim()).filter(Boolean);
    const cell=v=>v?esc(v):'<span class="sub">Not found</span>';
    function animateNumber(element,value){const start=Number(element.textContent)||0, end=Number(value)||0, began=performance.now();function tick(now){const progress=Math.min(1,(now-began)/350);element.textContent=Math.round(start+(end-start)*(1-Math.pow(1-progress,3)));if(progress<1)requestAnimationFrame(tick)}requestAnimationFrame(tick)}
    function showRows(rows){$('count').textContent=rows.length+' prospect'+(rows.length===1?'':'s')+' found'; if(!rows.length){results.innerHTML='<div class="empty"><div class="empty-icon">⌕</div>No verified businesses found for this search.</div>';return}
      results.innerHTML='<table class="results-table"><thead><tr><th>Business</th><th>Category</th><th>Website</th><th>Phone</th><th>Address</th><th>Email</th></tr></thead><tbody>'+rows.map(r=>'<tr><td><span class="name">'+esc(r.business_name)+'</span></td><td>'+esc(r.category)+'</td><td>'+(r.website?'<a class="link" href="'+esc(r.website)+'" target="_blank" rel="noreferrer">'+esc(r.website)+'</a>':'<span class="sub">Not published</span>')+'</td><td>'+cell(r.phone)+'</td><td>'+cell(r.address)+'</td><td>'+cell(r.email)+'</td></tr>').join('')+'</tbody></table>'}
    async function poll(){try{const s=await (await fetch('/api/jobs/'+jobId)).json(); const done=['completed','failed','cancelled'].includes(s.status); $('statusTitle').textContent=done?(s.status==='completed'?'Discovery complete':s.status[0].toUpperCase()+s.status.slice(1)):s.phase||'Scanning public sources'; $('statusCopy').textContent=s.error||('Searching, verifying and ranking candidates…'); $('activity').textContent=done?'Run finished':(s.phase||'Working'); $('pulse').style.display=done?'none':'block'; ['discovered','verified','complete','rejected'].forEach(k=>animateNumber($(k),s[k]||0)); $('bar').style.width=done?'100%':Math.min(95,Math.max(7,(s.verified/(s.discovered||1))*90))+'%'; if(done){clearInterval(timer);submit.disabled=false;cancel.hidden=true;download.hidden=s.status!=='completed'; if(s.status==='failed') results.innerHTML='<div class="error">The discovery failed: '+esc(s.error||'Unknown error')+'</div>'; else showRows(await (await fetch('/api/jobs/'+jobId+'/leads')).json())}}catch(e){$('statusCopy').textContent='Connection interrupted. Retrying…';$('activity').textContent='Reconnecting to the local worker…'}}
    form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;cancel.hidden=false;download.hidden=true;panel.style.display='block';results.innerHTML='<div class="empty"><div class="empty-icon">✦</div>Scanning public sources…</div>'; $('count').textContent='Discovery in progress'; const body={categories:split($('categories').value),locations:split($('locations').value)}; try{const r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error('Could not start discovery');jobId=(await r.json()).id;download.onclick=()=>location.href='/api/jobs/'+jobId+'/csv';poll();timer=setInterval(poll,1500)}catch(err){submit.disabled=false;$('statusCopy').textContent=err.message}});
    cancel.addEventListener('click',async()=>{if(jobId)await fetch('/api/jobs/'+jobId+'/cancel',{method:'POST'});cancel.disabled=true;$('statusCopy').textContent='Cancellation requested…'});
    document.querySelectorAll('.suggestion').forEach(button=>button.addEventListener('click',()=>{$('categories').value=button.dataset.category;$('locations').value=button.dataset.location;$('categories').focus()}));
    </script></body></html>
    """


@app.post("/api/jobs")
def create_job(request: JobRequest):
    job_id = uuid.uuid4().hex
    now = utc_now()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO jobs(id, categories, locations, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, ",".join(request.categories), ",".join(request.locations), "queued", now, now),
        )
    with _jobs_lock:
        _jobs[job_id] = {"cancelled": False}
    thread = threading.Thread(
        target=run_job,
        args=(job_id, request.categories, request.locations),
        daemon=True,
    )
    thread.start()
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with db() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Job not found")
    return dict(row)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(404, "Job not found")
        _jobs[job_id]["cancelled"] = True
    return {"id": job_id, "status": "cancelling"}


@app.get("/api/jobs/{job_id}/leads")
def get_leads(job_id: str):
    with db() as connection:
        rows = connection.execute(
            "SELECT business_name, category, website, phone, address, email FROM leads WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/jobs/{job_id}/diagnostics")
def get_diagnostics(job_id: str):
    with db() as connection:
        rows = connection.execute(
            "SELECT message, created_at FROM diagnostics WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/jobs/{job_id}/csv")
def download_csv(job_id: str):
    rows = get_leads(job_id)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
    )

const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");
const { execFileSync } = require("child_process");
const { WebSocketServer } = require("ws");
const pty = require("node-pty");

const DASHBOARD_DIR = path.join(__dirname, "dashboard", "dist");
const PORT = parseInt(process.env.ORCHESTRA_DASHBOARD_PORT || "8891", 10);
const MAC_SSH_TARGET = (process.env.ORCHESTRA_MAC_SSH_USER || "") + "@" + (process.env.ORCHESTRA_MAC_TAILSCALE_IP || "");
const MIME = { ".html":"text/html",".js":"application/javascript",".css":"text/css",".json":"application/json",".png":"image/png",".svg":"image/svg+xml",".ico":"image/x-icon",".woff2":"font/woff2",".woff":"font/woff",".webmanifest":"application/manifest+json",".map":"application/json" };

function getVpsAgents(){try{const o=execFileSync("tmux",["list-sessions","-F","#{session_name}"],{encoding:"utf-8",timeout:5000});const s=o.trim().split("\n").filter(x=>x.trim());return{agents:s.map(x=>({id:"vps:"+x,tier:"T2",name:x,machine:"vps",tmux_session:x,always_on:false,status:"running",current_task:null,tmux_alive:true,alive:true,inbox_count:0,machine_status:"online"})),total:s.length,online:s.length,mac_status:"offline"};}catch{return{agents:[],total:0,online:0,mac_status:"offline"};}}
function getVpsOutput(s,n){try{const o=execFileSync("tmux",["capture-pane","-t",s,"-p","-S","-"+n],{encoding:"utf-8",timeout:5000});return{agent_id:s,session:s,lines:o.trim().split("\n"),raw:o.trim()};}catch{return{agent_id:s,session:s,lines:[],raw:"",error:"not found"};}}
function injectVps(s,t){try{execFileSync("tmux",["send-keys","-t",s,t,"Enter"],{timeout:5000});return{injected:true};}catch(e){return{injected:false,error:e.message};}}



function serveStatic(req,res){const up=req.url.split("?")[0];const fp=path.join(DASHBOARD_DIR,up==="/"?"index.html":up);if(!fp.startsWith(DASHBOARD_DIR)){res.writeHead(403);res.end("Forbidden");return;}if(fs.existsSync(fp)&&fs.statSync(fp).isFile()){const e=path.extname(fp);const h={"Content-Type":MIME[e]||"application/octet-stream"};h["Cache-Control"]=e===".html"?"no-cache, no-store, must-revalidate":"public, max-age=31536000, immutable";const ae=(req.headers["accept-encoding"]||"");if(ae.includes("gzip")&&fs.existsSync(fp+".gz")){h["Content-Encoding"]="gzip";res.writeHead(200,h);res.end(fs.readFileSync(fp+".gz"));}else{res.writeHead(200,h);res.end(fs.readFileSync(fp));}return;}if(up.startsWith("/assets/")){res.writeHead(404);res.end("Not found");return;}try{res.writeHead(200,{"Content-Type":"text/html","Cache-Control":"no-cache"});res.end(fs.readFileSync(path.join(DASHBOARD_DIR,"index.html")));}catch{res.writeHead(404);res.end("Not found");}}

const VPS_API = "http://127.0.0.1:8888";
function proxyTo(base,req,res,timeoutMs){const u=base+req.url;const o=new URL(u);const opts={hostname:o.hostname,port:o.port,path:o.pathname+(o.search||""),method:req.method,headers:{...req.headers,host:o.host},timeout:timeoutMs||30000};const p=http.request(opts,(r)=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});p.on("error",(e)=>{res.writeHead(502,{"Content-Type":"application/json"});res.end(JSON.stringify({error:"upstream unreachable",detail:e.message}));});p.on("timeout",()=>{p.destroy();});req.pipe(p);}
function proxyToLocal(req,res){proxyTo(VPS_API,req,res);}

const server = http.createServer((req,res)=>{
  // VPS-specific routes go directly to local API (not Mac)
  if(req.url.startsWith("/api/system/vps-auth/"))proxyToLocal(req,res);
  else if(req.url.startsWith("/api/adaptive/"))proxyToLocal(req,res);
  else if(req.url.startsWith("/api/uploads"))proxyToLocal(req,res);
  else if(req.url.startsWith("/api/"))proxyToLocal(req,res);
  else serveStatic(req,res);
});


const { execFile: execFileAsync } = require("child_process");

// Async history capture
function sendHistory(ws, sess, mach) {
  const cb = (err, stdout) => {
    if (err || !stdout || !stdout.trim()) return;
    if (ws.readyState !== 1) return;
    const lines = stdout.trim().split("\n").length;
    ws.send("\x1b[33m[History: " + lines + " lines]\x1b[0m\r\n");
    ws.send(stdout.replace(/\n/g, "\r\n"));
    ws.send("\r\n\x1b[33m[Live]\x1b[0m\r\n\r\n");
  };
  if (mach === "mac") {
    execFileAsync("ssh", ["-o","ConnectTimeout=5","-o","StrictHostKeyChecking=no","-o","IdentitiesOnly=yes","-i",process.env.HOME+"/.ssh/id_ed25519",MAC_SSH_TARGET,"tmux capture-pane -t "+sess+" -p -S -500 2>/dev/null || true"], {encoding:"utf-8",timeout:10000}, cb);
  } else {
    execFileAsync("tmux", ["capture-pane","-t",sess,"-p","-S","-500"], {encoding:"utf-8",timeout:5000}, cb);
  }
}

// Set aggressive-resize on a tmux session (async, fire-and-forget)
function setAggressiveResize(sess, mach) {
  if (mach === "mac") {
    execFileAsync("ssh", ["-o","ConnectTimeout=3","-o","StrictHostKeyChecking=no","-o","IdentitiesOnly=yes","-i",process.env.HOME+"/.ssh/id_ed25519",MAC_SSH_TARGET,"tmux set-option -t "+sess+" aggressive-resize on 2>/dev/null || true"], {timeout:8000}, ()=>{});
  } else {
    execFileAsync("tmux", ["set-option","-t",sess,"aggressive-resize","on"], {timeout:3000}, ()=>{});
  }
}

// B3 voice bridge: forward any /api/* WS upgrade (e.g. /api/voice/live) to the
// local API on :8888. prependListener runs before the terminal WSS's own
// 'upgrade' handler below, so this must scope itself to /api/* and fall
// through (return without handling) for everything else, incl. /ws/terminal.
server.prependListener("upgrade", (req, socket, head) => {
  if (!req.url.startsWith("/api/")) return; // fall through to /ws/terminal etc.
  const p = http.request({ hostname: "127.0.0.1", port: 8888, path: req.url, method: "GET", headers: req.headers });
  p.on("upgrade", (r, s, h) => { socket.write("HTTP/1.1 101 Switching Protocols\r\n" + Object.entries(r.headers).map(([k, v]) => k + ": " + v).join("\r\n") + "\r\n\r\n"); if (h.length) socket.write(h); s.pipe(socket); socket.pipe(s); });
  p.on("error", () => socket.destroy());
  p.end();
});

// WebSocket terminal with ws library + node-pty
const wss = new WebSocketServer({ server, path: "/ws/terminal" });
wss.on("connection", (ws, req) => {
  const url = new URL(req.url, "http://localhost");
  const session = url.searchParams.get("session");
  const machine = url.searchParams.get("machine") || "local";
  const initCols = parseInt(url.searchParams.get("cols")) || 80;
  const initRows = parseInt(url.searchParams.get("rows")) || 24;
  if (!session) { ws.send("\r\n\x1b[31mError: session required\x1b[0m\r\n"); ws.close(); return; }
  console.log("[ws-terminal] " + machine + ":" + session);
  sendHistory(ws, session, machine);
  setAggressiveResize(session, machine);

  let shell, args;
  if (machine === "mac") {
    shell = "ssh"; args = ["-o","ConnectTimeout=5","-o","StrictHostKeyChecking=no","-o","IdentitiesOnly=yes","-i",process.env.HOME+"/.ssh/id_ed25519","-t",MAC_SSH_TARGET,"tmux attach -t "+session];
  } else {
    shell = "tmux"; args = ["attach","-t",session];
  }
  const ptyEnv = { ...process.env, TERM: "xterm-256color" };
  delete ptyEnv.TMUX;
  delete ptyEnv.TMUX_PANE;
  const term = pty.spawn(shell, args, { name:"xterm-256color", cols:initCols, rows:initRows, cwd:process.env.HOME, env:ptyEnv });
  console.log("[ws-terminal] pid=" + term.pid);
  term.onData(d => { if(ws.readyState===1) ws.send(d); });
  ws.on("message", m => { const s=m.toString(); if(s.startsWith("{")){try{const c=JSON.parse(s);if(c.type==="resize"&&c.cols&&c.rows){term.resize(c.cols,c.rows);return;}}catch{}} term.write(s); });
  const cleanup = () => { try{term.kill();}catch{} };
  ws.on("close", cleanup); ws.on("error", cleanup);
  term.onExit(({exitCode}) => {
    console.log("[ws-terminal] exit "+session+" code="+exitCode);
    if(ws.readyState===1){
      // Check if reincarnation is pending (handoff file exists and is fresh)
      const handoffPath = require("path").join(__dirname, "state", "handoffs", session + ".json");
      let reincarnating = false;
      try {
        const stat = require("fs").statSync(handoffPath);
        reincarnating = (Date.now() - stat.mtimeMs) < 300000; // < 5 min old
      } catch {}

      if (reincarnating) {
        ws.send("\r\n\x1b[33m\x1b[1m⟳ Context full. Reincarnating...\x1b[0m\r\n");
        ws.send("\x1b[90mYour agent is transferring its memory to a new instance.\x1b[0m\r\n");
        ws.send("\x1b[90mThis takes about 10 seconds. Your work is preserved.\x1b[0m\r\n");
        // Keep WS open — auto-reconnect will happen when new tmux session appears
        setTimeout(() => {
          // Try to reattach to the new session
          try {
            const newTerm = pty.spawn("tmux", ["attach", "-t", session], {
              name: "xterm-256color", cols: initCols, rows: initRows,
              cwd: process.env.HOME, env: {...process.env, TERM: "xterm-256color"},
            });
            ws.send("\r\n\x1b[32m\x1b[1m✓ Reincarnated. Continuing...\x1b[0m\r\n\r\n");
            newTerm.onData(d => { if(ws.readyState===1) ws.send(d); });
            ws.on("message", m => { const s=m.toString(); if(s.startsWith("{")){try{const c=JSON.parse(s);if(c.type==="resize"&&c.cols&&c.rows){newTerm.resize(c.cols,c.rows);return;}}catch{}} newTerm.write(s); });
            const newCleanup = () => { try{newTerm.kill();}catch{} };
            ws.removeAllListeners("close"); ws.removeAllListeners("error");
            ws.on("close", newCleanup); ws.on("error", newCleanup);
            newTerm.onExit(() => { if(ws.readyState===1){ws.send("\r\n\x1b[33m[Session ended]\x1b[0m\r\n");ws.close();} });
          } catch(e) {
            ws.send("\r\n\x1b[31mReincarnation failed: "+e.message+"\x1b[0m\r\n");
            ws.close();
          }
        }, 12000); // Wait 12s for spawn to complete
      } else {
        ws.send("\r\n\x1b[33m[Session ended]\x1b[0m\r\n");
        ws.close();
      }
    }
  });
});

server.listen(PORT, "127.0.0.1", () => console.log("Dashboard proxy on :" + PORT + " (ws+pty)"));

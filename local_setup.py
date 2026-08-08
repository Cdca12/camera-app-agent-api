"""A minimal same-origin setup UI for the CameraApp edge agent.

It intentionally lives in the agent instead of Vercel: browser HTTPS pages
cannot reliably configure an HTTP device on a private LAN. The technical key is
kept only in browser memory and is sent in the request header for this session.
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse


SETUP_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CameraApp · Instalación local</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background:#050b19; color:#f6f8ff; }
    * { box-sizing:border-box; } body { margin:0; min-height:100vh; background:radial-gradient(circle at 10% 0,#123b5b 0,transparent 33%),#050b19; }
    main { max-width:1000px; margin:0 auto; padding:40px 20px 72px; } h1 { margin:6px 0 8px; font-size:clamp(28px,5vw,42px); }
    h2 { margin:0 0 18px; font-size:20px; } p { color:#a4b1ca; line-height:1.55; } .eyebrow { color:#3ebcf6; font-size:12px; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
    .card { margin-top:22px; padding:24px; border:1px solid #31415f; border-radius:20px; background:#0d172bde; box-shadow:0 18px 50px #0004; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; } .grid.three { grid-template-columns:repeat(3,minmax(0,1fr)); }
    label { display:grid; gap:8px; color:#c7d1e3; font-size:14px; font-weight:700; } input, select { width:100%; padding:13px 14px; border:1px solid #374763; border-radius:11px; background:#070e20; color:#f6f8ff; font:inherit; }
    button { border:0; border-radius:11px; padding:12px 17px; font:inherit; font-weight:800; color:#062139; background:#42bcf4; cursor:pointer; } button.secondary { color:#c5ecff; border:1px solid #27678f; background:#102a43; } button:disabled { opacity:.5; cursor:not-allowed; }
    .row { display:flex; flex-wrap:wrap; align-items:end; gap:12px; } .row > label { flex:1 1 190px; } .status { margin-top:12px; padding:12px 14px; border-radius:10px; background:#111d34; color:#b6c7e5; } .status.error { background:#3a1f2c; color:#ffb6bf; } .status.ok { background:#10342d; color:#a8f1cb; }
    .camera-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(205px,1fr)); gap:14px; margin-top:18px; } .camera { overflow:hidden; border:1px solid #32445f; border-radius:14px; background:#081225; cursor:pointer; } .camera.selected { border-color:#40c1fb; box-shadow:0 0 0 2px #40c1fb55; } .camera img { display:block; width:100%; aspect-ratio:16/9; object-fit:cover; background:#060b16; } .camera div { padding:12px; } .camera strong,.camera small { display:block; } .camera small { color:#9caac4; margin-top:4px; }
    .muted { color:#8492ad; } .hidden { display:none; } #setup-content { opacity:.45; pointer-events:none; transition:opacity .2s; } #setup-content.ready { opacity:1; pointer-events:auto; }
    @media (max-width:680px) { main { padding:26px 14px 50px; } .card { padding:18px; } .grid,.grid.three { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">CameraApp · Instalación técnica</div>
    <h1>Configurar agente local</h1>
    <p>Esta pantalla opera directamente con este equipo dentro de la red local. No uses ni compartas aquí credenciales del dashboard público.</p>

    <section class="card">
      <h2>Acceso técnico</h2>
      <div class="row"><label>Clave técnica local<input id="local-key" type="password" autocomplete="off" placeholder="Solo se conserva durante esta sesión"></label><button id="connect" type="button">Conectar</button></div>
      <div id="access-status" class="status">Verificando el estado del agente…</div>
    </section>

    <div id="setup-content">
      <section class="card">
        <div class="eyebrow">1 · Tienda local</div><h2>Selecciona o crea la tienda del agente</h2>
        <div class="row"><label>Tienda<select id="store-select"><option>Cargando…</option></select></label><button id="reload-stores" class="secondary" type="button">Actualizar</button></div>
        <details style="margin-top:20px"><summary class="muted">Crear una tienda local</summary><div class="grid" style="margin-top:14px"><label>Nombre<input id="store-name" placeholder="Hikvision Piedra"></label><label>Código<input id="store-code" placeholder="hikvision-piedra"></label></div><button id="create-store" style="margin-top:14px" type="button">Crear tienda</button></details>
      </section>

      <section class="card">
        <div class="eyebrow">2 · Conexión RTSP / NVR</div><h2>Guardar conexión en este equipo</h2>
        <div class="grid"><label>Host o IP<input id="host" autocomplete="off"></label><label>Usuario<input id="username" autocomplete="off"></label><label>Contraseña<input id="password" type="password" autocomplete="new-password"></label><label>Puerto<input id="port" value="554"></label></div>
        <label style="margin-top:16px">Ruta RTSP<input id="path-template" value="/Streaming/Channels/{channel}"></label>
        <button id="save-config" style="margin-top:16px" type="button">Guardar conexión</button><div id="config-status" class="status hidden"></div>
      </section>

      <section class="card">
        <div class="eyebrow">3 · Cámaras</div><h2>Escanear y registrar cámara</h2>
        <div class="row"><label>Canales a revisar<input id="max-channels" type="number" min="1" max="8" value="8"></label><button id="scan" type="button">Escanear cámaras</button></div>
        <div id="scan-status" class="status hidden"></div><div id="camera-grid" class="camera-grid"></div>
        <div id="add-camera-area" class="hidden" style="margin-top:18px"><div class="row"><label>Nombre de la cámara<input id="camera-name" value="Entrada principal"></label><button id="add-camera" type="button">Agregar cámara seleccionada</button></div></div>
      </section>
    </div>
  </main>
  <script>
    const state = { key: '', stores: [], selectedStoreId: null, selectedCamera: null };
    const $ = (id) => document.getElementById(id);
    const setStatus = (id, text, kind = '') => { const node = $(id); node.textContent = text; node.className = `status ${kind}`; };
    const request = async (path, options = {}) => {
      const headers = { ...(options.body ? {'Content-Type':'application/json'} : {}), ...(state.key ? {'X-CameraApp-Local-Key':state.key} : {}), ...(options.headers || {}) };
      const response = await fetch(path, {...options, headers});
      const contentType = response.headers.get('content-type') || '';
      const payload = contentType.includes('application/json') ? await response.json() : {};
      if (!response.ok) throw new Error(payload.detail || 'No fue posible completar la operación.');
      return payload;
    };
    const selectedStore = () => Number($('store-select').value || state.selectedStoreId || 0);
    async function loadStores() {
      const data = await request('/stores'); state.stores = data.stores || [];
      const select = $('store-select'); select.innerHTML = '';
      if (!state.stores.length) select.innerHTML = '<option value="">No hay tiendas locales</option>';
      state.stores.forEach((store) => { const option = document.createElement('option'); option.value = store.id; option.textContent = `${store.name} · ${store.code}`; select.append(option); });
      if (state.selectedStoreId && state.stores.some((store) => store.id === state.selectedStoreId)) select.value = state.selectedStoreId;
      state.selectedStoreId = selectedStore() || null;
      if (state.selectedStoreId) await loadConfig();
    }
    async function loadConfig() {
      const storeId = selectedStore(); if (!storeId) return;
      const data = await request(`/stores/${storeId}/camera-config`); const config = data.config || {};
      ['host','username','port','path-template'].forEach((id) => { const key = id.replace('-', '_'); if (config[key]) $(id).value = config[key]; });
      $('password').value = '';
    }
    function requireStore() { const storeId = selectedStore(); if (!storeId) throw new Error('Crea o selecciona una tienda local primero.'); return storeId; }
    function renderCameras(cameras) {
      const grid = $('camera-grid'); grid.innerHTML = ''; state.selectedCamera = null; $('add-camera-area').classList.add('hidden');
      cameras.forEach((camera) => { const card = document.createElement('article'), image = document.createElement('img'), details = document.createElement('div'), name = document.createElement('strong'), channel = document.createElement('small'); card.className = 'camera'; image.alt = 'Vista previa temporal'; image.src = camera.preview_image; name.textContent = camera.name; channel.textContent = `Canal ${camera.channel}`; details.append(name, channel); card.append(image, details); card.onclick = () => { document.querySelectorAll('.camera').forEach((node) => node.classList.remove('selected')); card.classList.add('selected'); state.selectedCamera = camera; $('add-camera-area').classList.remove('hidden'); $('camera-name').value = camera.name; }; grid.append(card); });
    }
    $('connect').onclick = async () => { state.key = $('local-key').value.trim(); try { const health = await request('/health'); setStatus('access-status', health.local_access_protected ? 'Acceso técnico protegido y agente disponible.' : 'Agente disponible. Configura una clave técnica antes de instalación permanente.', 'ok'); $('setup-content').classList.add('ready'); await loadStores(); } catch (error) { setStatus('access-status', error.message, 'error'); } };
    $('reload-stores').onclick = () => loadStores().catch((error) => setStatus('access-status', error.message, 'error'));
    $('store-select').onchange = () => { state.selectedStoreId = selectedStore(); loadConfig().catch((error) => setStatus('config-status', error.message, 'error')); };
    $('create-store').onclick = async () => { try { const name = $('store-name').value.trim(), code = $('store-code').value.trim(); if (!name || !code) throw new Error('Escribe nombre y código de la tienda.'); const store = await request('/stores',{method:'POST',body:JSON.stringify({name,code,timezone:'America/Mazatlan'})}); state.selectedStoreId=store.id; await loadStores(); setStatus('access-status','Tienda local creada.','ok'); } catch(error) { setStatus('access-status',error.message,'error'); } };
    $('save-config').onclick = async () => { try { const storeId=requireStore(); const payload={host:$('host').value.trim(),username:$('username').value.trim(),password:$('password').value,port:$('port').value.trim(),path_template:$('path-template').value.trim()}; if (!payload.host || !payload.username || !payload.password || !payload.path_template) throw new Error('Completa host, usuario, contraseña y ruta RTSP.'); await request(`/stores/${storeId}/camera-config`,{method:'PUT',body:JSON.stringify(payload)}); $('password').value=''; setStatus('config-status','Conexión guardada localmente.','ok'); } catch(error) { setStatus('config-status',error.message,'error'); } };
    $('scan').onclick = async () => { try { const storeId=requireStore(); setStatus('scan-status','Escaneando cámaras…'); const data=await request('/camera-channels/scan',{method:'POST',body:JSON.stringify({store_id:storeId,max_channels:Number($('max-channels').value || 8)})}); renderCameras(data.cameras || []); setStatus('scan-status', data.cameras?.length ? `${data.cameras.length} cámara(s) encontrada(s). Selecciona una.` : 'No se detectaron cámaras. Revisa la conexión RTSP o agrega el canal más tarde.', data.cameras?.length ? 'ok' : ''); } catch(error) { setStatus('scan-status',error.message,'error'); } };
    $('add-camera').onclick = async () => { try { const storeId=requireStore(), camera=state.selectedCamera, name=$('camera-name').value.trim(); if(!camera || !name) throw new Error('Selecciona una cámara y define su nombre.'); await request(`/stores/${storeId}/cameras`,{method:'POST',body:JSON.stringify({name,channel:camera.channel,location:'',is_active:true,collection_enabled:false})}); setStatus('scan-status','Cámara agregada con recolección desactivada.','ok'); } catch(error) { setStatus('scan-status',error.message,'error'); } };
    $('connect').click();
  </script>
</body>
</html>"""


def setup_page() -> HTMLResponse:
    return HTMLResponse(SETUP_HTML)

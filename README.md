---
title: CameraApp API
emoji: 📷
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# CameraApp API

FastAPI + DeepFace API para estimar edad y género desde un frame enviado por multipart/form-data.

## Ejecutable Windows

Para correr la API en una PC remota del negocio como `CameraAppAPI.exe`, revisa
[`README_EXE.md`](README_EXE.md).

## Endpoints

- `GET /health`
- `GET /stores`
- `GET /dashboard/summary`
- `GET /dashboard/daily`
- `GET /test/stores`
- `GET /test/dashboard/summary`
- `GET /test/dashboard/daily`
- `GET /configuration`
- `GET /test/configuration`
- `POST /configuration/stores`
- `POST /test/configuration/stores`
- `PUT /configuration/stores/{store_id}/primary-camera`
- `PUT /test/configuration/stores/{store_id}/primary-camera`
- `POST /camera-config`
- `POST /test/camera-config`
- `POST /camera-channels/scan`
- `POST /analyze-frame`
- `GET /camera-frame`
- `POST /analyze-camera-frame`
- `POST /watch-camera-frame`
- `POST /watch-uploaded-frame`

## Base de datos local

El dashboard usa SQLite, incluido con Python, por lo que no requiere instalar ni
ejecutar un servidor de base de datos adicional. La operación real usa
`data/camera_app_operational.db`; las rutas con prefijo `/test` usan una base independiente
en `data/camera_app_test.db`. Ninguna lectura ni escritura de `/test` afecta la
base operativa.

Para crear las tablas y cargar 30 días de datos simulados:

```bash
source .venv/bin/activate
python scripts/seed_database.py
```

La carga reemplaza los datos simulados anteriores para que el entorno de
desarrollo siempre sea reproducible. Puedes cambiar el periodo:

```bash
python scripts/seed_database.py --days 60
```

Para consultar las tiendas:

```http
GET /stores
```

Para obtener el resumen del dashboard:

```http
GET /dashboard/summary?store_id=1&date_from=2026-07-19&date_to=2026-07-19
```

Para obtener el histórico diario de la página de detalle:

```http
GET /dashboard/daily?store_id=1&date_from=2026-07-01&date_to=2026-07-19
```

El histórico regresa los días del más reciente al más antiguo e incluye
conteo, variación contra el día anterior, hora pico, género predominante y
rango de edad predominante. El rango máximo permitido es de 366 días.

## Configuración local

`GET /configuration` devuelve las tiendas, su cámara principal y el estado del
servicio local. Para crear una tienda:

```json
POST /configuration/stores
{
  "name": "Maja Nueva Sucursal",
  "code": "maja-nueva-sucursal",
  "timezone": "America/Mazatlan"
}
```

Para guardar la cámara principal de una tienda:

```json
PUT /configuration/stores/1/primary-camera
{
  "name": "Entrada principal",
  "channel": "101",
  "location": "Acceso principal",
  "is_active": true
}
```

Estas configuraciones se guardan en SQLite. Las credenciales RTSP se cifran
localmente con Fernet y nunca se exponen en las respuestas ni se sincronizan
con CameraApp Central. La llave Fernet se guarda junto a la base de datos y
debe respaldarse con ella.

Si no se envían fechas, el endpoint utiliza el día actual. Los nombres técnicos
de tablas, columnas y valores internos están en inglés; las etiquetas listas
para presentar en la interfaz se devuelven en español.

Opcionalmente, la ubicación de la base puede configurarse con:

```env
CAMERA_APP_DB_PATH=/ruta/local/camera_app_operational.db
CAMERA_APP_TEST_DB_PATH=/ruta/local/camera_app_test.db
```

Al iniciar la API por primera vez, la base de pruebas se llena con los datos
simulados existentes. Las detecciones de cámara siempre se guardan únicamente
en la base operativa.

## Cámara de vigilancia

El frontend puede configurar el NVR/cámara en runtime:

```http
POST /camera-config
Content-Type: application/json
```

```json
{
  "host": "192.168.1.64",
  "username": "usuario",
  "password": "password",
  "port": "554",
  "path_template": "/Streaming/Channels/{channel}"
}
```

La contraseña se usa solo para construir la URL RTSP dentro del backend. No se
regresa en la respuesta del endpoint.

Con una configuración RTSP activa, el frontend puede descubrir canales del NVR
sin pedirle al administrador que los adivine:

```http
POST /camera-channels/scan
Content-Type: application/json
```

```json
{
  "max_channels": 16
}
```

La búsqueda prueba los primeros ocho canales Hikvision habituales `101`, `201`,
`301`, etc. Cada canal responde únicamente si puede entregar un frame. El
escaneo se detiene alrededor de los cuatro segundos para mantener una respuesta
ágil; el tiempo por intento se controla con `CAMERA_SCAN_TIMEOUT_MS` (por
defecto 450 ms).

El frontend puede seguir usando:

```http
GET /camera-frame?channel=<canal>&t=<timestamp>
POST /analyze-camera-frame?channel=<canal>&camera_name=<nombre>
```

La API resuelve la fuente de la cámara con esta prioridad:

1. `CAMERA_SOURCE_CHANNEL_<canal>`
2. Configuración runtime recibida en `POST /camera-config`
3. `CAMERA_SOURCE_TEMPLATE`
4. `CAMERA_SOURCE`

También puedes configurar la fuente de video directamente con
`CAMERA_SOURCE`. Puede ser una URL RTSP/HTTP de la cámara o DVR/NVR:

```env
CAMERA_SOURCE=rtsp://usuario:password@192.168.1.64:554/Streaming/Channels/101
```

Para usar canales de un NVR desde el frontend, puedes configurar una plantilla:

```env
CAMERA_SOURCE_TEMPLATE=rtsp://usuario:password@192.168.1.64:554/Streaming/Channels/{channel}
```

También puedes configurar fuentes por canal:

```env
CAMERA_SOURCE_CHANNEL_502=rtsp://usuario:password@192.168.1.64:554/Streaming/Channels/502
CAMERA_SOURCE_CHANNEL_302=rtsp://usuario:password@192.168.1.64:554/Streaming/Channels/302
```

También puedes inicializar la configuración runtime desde variables separadas:

```env
CAMERA_HOST=192.168.1.64
CAMERA_USERNAME=usuario
CAMERA_PASSWORD=password
CAMERA_RTSP_PORT=554
CAMERA_PATH_TEMPLATE=/Streaming/Channels/{channel}
```

Opcionalmente puedes ajustar el tiempo de espera:

```env
CAMERA_TIMEOUT_MS=5000
```

`GET /camera-frame` devuelve un JPEG del frame actual. También acepta
`?channel=<canal>`. `POST /analyze-camera-frame` captura un frame y lo analiza
con DeepFace; también acepta `?channel=<canal>&camera_name=<nombre>`.

## Monitoreo ligero de caras nuevas

Para revisar la cámara con menor costo, usa:

```http
POST /watch-camera-frame?channel=<canal>&camera_name=<nombre>
```

Para revisar frames enviados desde la cámara del dispositivo, usa:

```http
POST /watch-uploaded-frame
Content-Type: multipart/form-data
```

```text
file=<imagen jpg/png>
```

Este endpoint captura un frame, detecta caras con OpenCV y mantiene un cache en
memoria por cámara/canal o por cámara del dispositivo. Si no hay caras nuevas
responde:

```json
{
  "success": true,
  "has_new_faces": false,
  "faces": []
}
```

Si detecta una cara nueva, entonces corre DeepFace para edad/género y responde:

```json
{
  "success": true,
  "has_new_faces": true,
  "faces": []
}
```

Cada cara nueva detectada por `POST /watch-camera-frame` o
`POST /watch-uploaded-frame` también se guarda en SQLite como un evento con
`data_source = captured`. Por defecto se asocia a la tienda `store_id=1`; para
pruebas con otra tienda se puede enviar `?store_id=<id>`. Las caras ya vistas
durante el TTL del cache no se vuelven a registrar.

Variables opcionales:

```env
FACE_CACHE_TTL_SECONDS=300
FACE_MATCH_THRESHOLD=0.18
FACE_DETECT_WIDTH=640
```

`FACE_CACHE_TTL_SECONDS` define cuánto tiempo una cara vista queda en cache.
`FACE_MATCH_THRESHOLD` ajusta qué tan parecidas deben ser dos caras para tratarse
como la misma persona. `FACE_DETECT_WIDTH` reduce el frame antes de detectar
caras para que el monitoreo sea más ligero.

Para desarrollo local puedes guardar `CAMERA_SOURCE` en un archivo `.env`.

## Sincronización con CameraApp Central

El agente conserva la detección y la configuración RTSP localmente. Cuando se
configura la sincronización, únicamente envía a CameraApp Central el estado del
servicio, metadatos no sensibles de las cámaras y detecciones con edad y género
completos. Los frames, direcciones RTSP y credenciales no salen del equipo.

La clave del agente se enrola para una tienda central específica; por eso el
equipo no necesita guardar el identificador central de la tienda en su archivo
local de configuración.

Parte de `.env.example` para preparar el agente. Las detecciones confirmadas por
la API central se marcan en SQLite; si la red no está disponible, se conservan en
una cola local y se reintentan en el siguiente ciclo.

## Operación en Raspberry Pi

La instalación de producción debe conservar su estado fuera del checkout:

- SQLite y llave Fernet: `/var/lib/cameraapp`.
- Pesos descargados por DeepFace: `/var/lib/cameraapp/deepface`.
- Variables privadas: `/etc/cameraapp/agent.env` con permisos restringidos.

En la Raspberry, tras clonar el repositorio y crear el entorno virtual, instala
el servicio versionado con:

```bash
sudo scripts/install_agent.sh
```

El archivo `deploy/systemd/cameraapp-agent.service.template` se convierte en la
unidad `cameraapp-agent`; esta debe iniciar automáticamente y responder en
`/health`. El primer inicio puede tardar mientras TensorFlow prepara sus
componentes.

Antes de usar una cámara real, ejecuta el benchmark con una imagen temporal que
no se sube ni se guarda por CameraApp:

```bash
.venv/bin/python scripts/benchmark_inference.py --image /ruta/temporal/frame.jpg
```

El resultado JSON contiene duración y métricas de equipo, no edad, género ni
recortes faciales. Evalúa RAM, swap, temperatura y latencia antes de habilitar
la recolección continua.

Para actualizar de forma recuperable:

```bash
sudo scripts/update_agent.sh
```

El script exige un checkout limpio, realiza `git pull --ff-only`, instala sin
cache duplicada usando el disco, reinicia el servicio y valida `/health`. Si
falla, restaura la revisión previa y la copia local de SQLite con su llave.

## Deploy con frontend en Vercel

Configura el frontend para llamar a la URL publica del backend, no a una ruta relativa de Vercel.
Por ejemplo:

```env
VITE_API_BASE_URL=https://cdca12-camera-app-api.hf.space
```

El backend acepta por default solo el dominio del frontend:

```env
CORS_ORIGINS=https://camera-app-front.vercel.app
```

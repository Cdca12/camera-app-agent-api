# Roadmap de validación y despliegue de CameraApp

Este documento define cómo validar un nuevo pipeline de analítica en Maja sin
interrumpir la operación actual y conserva como objetivo futuro los tres modelos
de despliegue de CameraApp: local, centralizado y cloud.

## Principio de validación

Los pipelines no se compararán usando una instalación `prod` contra otra `dev`.
La comparación debe ocurrir sobre la misma cámara, durante las mismas ventanas de
tiempo y bajo las mismas condiciones reales de Maja.

La estrategia recomendada es:

- `legacy`: pipeline actual de detección facial y estimación demográfica. Continúa
  alimentando las métricas visibles mientras se obtiene la línea base.
- `candidate`: nuevo pipeline de detección de personas, tracking y cruce de línea.
- `shadow`: modo de ejecución del pipeline candidato en producción sin modificar
  el dashboard, alertas ni agregados oficiales.
- `manual`: conteo humano utilizado como fuente de verdad para evaluar ambos
  pipelines.

El pipeline actual y el candidato son dos implementaciones analíticas, no dos
ambientes completos. `dev` se reserva para desarrollar y reproducir material de
prueba; `prod + shadow` se usa para la comparación real en Maja.

### Estado actual del agente

CameraApp ya separa la base operacional (`CAMERA_APP_DB_PATH`) de la base de
prueba (`CAMERA_APP_TEST_DB_PATH`), pero esa separación no implementa por sí sola
una comparación de modelos. El monitor de recolección actual procesa las cámaras
operacionales con el pipeline facial; todavía no existe un runner continuo para
el pipeline candidato de persona, tracking y cruce de línea.

Antes del piloto deben añadirse cuatro piezas mínimas:

1. Una captura RTSP compartida o, temporalmente, una segunda captura sincronizada.
2. Un runner `candidate-shadow` que nunca escriba en los agregados oficiales.
3. Tablas o archivos de eventos shadow con versión de modelo y configuración.
4. Una exportación que alinee `legacy`, `candidate` y conteo manual por tiempo.

No hace falta duplicar CameraApp Central ni crear dos tiendas Maja. Si se necesita
aislamiento físico por capacidad, el segundo pipeline puede ejecutarse en otra
computadora de la LAN y seguir siendo conceptualmente parte del mismo piloto
`prod + shadow`.

## Arquitectura del piloto en Maja

Un solo proceso de captura debe leer el stream RTSP y distribuir los frames a los
dos pipelines. Esto evita abrir sesiones innecesarias contra el NVR y permite
atribuir a cada evento la misma cámara y reloj.

```text
Cámara/NVR
    |
Captura RTSP única
    |--------------------------|
Pipeline legacy                Pipeline candidate
cara + edad/género             persona + tracker + línea
    |                          |
métricas oficiales             eventos shadow aislados
    |                          |
    |------------- comparación con conteo manual
```

Si la Raspberry no tiene capacidad para ejecutar ambos pipelines, se mantendrá el
agente actual en la Raspberry y el candidato correrá temporalmente en una segunda
computadora dentro de la misma LAN. Ambos deben usar la misma fuente, cámara,
zona horaria y periodo de validación.

### Separación de datos

Los eventos shadow no deben insertarse en las tablas ni agregados oficiales. Cada
corrida de validación debe conservar como mínimo:

- Identificador de corrida.
- Pipeline, versión del modelo y hash/configuración.
- Tienda, cámara, zona y línea virtual.
- Timestamp, dirección `entry`/`exit`, confianza y latencia.
- Uso de CPU, RAM, temperatura y frames descartados.
- Conteo manual correspondiente por evento o intervalo.

Los identificadores de tracking son efímeros y locales. Los frames o clips de
depuración, si se habilitan para una validación autorizada, deben permanecer
locales, tener retención corta y eliminarse al cerrar la auditoría.

## Protocolo de validación manual

1. Elegir una sola entrada y fijar una línea virtual que no cambie durante la
   prueba.
2. Definir de antemano cómo se tratan empleados, reingresos, niños, grupos y
   personas que cruzan parcialmente la línea.
3. Obtener conteo manual de entradas y salidas. Para el primer ajuste pueden
   usarse bloques de 15 minutos; para validar eventos individuales se registrará
   el timestamp de cada cruce.
4. Cubrir apertura, horas tranquilas, hora pico, cambios de iluminación, grupos,
   oclusiones y cierre.
5. Ejecutar primero una prueba corta de 2 a 3 días para encontrar fallas obvias.
   Antes de promover el candidato, completar un periodo representativo de 1 a 2
   semanas.
6. Comparar por cámara, franja horaria, dirección y condición difícil; no usar
   únicamente el total diario.

### Métricas de aceptación

- Error firmado: predicción menos conteo manual.
- WAPE: suma de errores absolutos dividida entre el total manual.
- Precisión y recall de eventos emparejados dentro de una tolerancia temporal.
- Error separado para entradas y salidas.
- Disponibilidad del pipeline y periodos sin datos.
- Latencia, CPU, RAM, temperatura y frames descartados.

El objetivo inicial del candidato será un WAPE de 5% o menor en condiciones
representativas, sin degradar la estabilidad del agente. Las condiciones difíciles
se reportarán por separado y deberán tener causas conocidas antes del rollout.

## Promoción del nuevo pipeline

1. `legacy primary`: estado actual; el candidato todavía no participa.
2. `legacy + candidate shadow`: ambos corren, pero solo legacy publica métricas.
3. `candidate primary + legacy fallback`: el nuevo conteo es oficial y el pipeline
   anterior queda disponible temporalmente para diagnóstico.
4. `candidate only`: se retira el conteo legacy. La demografía queda como módulo
   opcional y nunca condiciona el conteo de tráfico.

El cambio de etapa exige un informe de validación reproducible con versión de
modelo, configuración de cámara y comparación manual.

## Evolución del modelo analítico

### Prioridad 1: conteo defendible

- Detector de persona completa.
- Tracking multiobjeto dentro de cada cámara.
- Cruce de línea y dirección real.
- Configuración de zonas y líneas por cámara.
- Conteo independiente de edad o género.
- Herramienta de auditoría y reproducción local.

### Prioridad 2: métricas comerciales

- Integración con POS por intervalos y tienda.
- Conversión de tráfico a tickets.
- Permanencia y rebote por zona.
- Monitoreo de calidad de cámara y cobertura.

### Prioridad 3: analítica avanzada

- Colas, saturación y alertas operativas.
- Mapas de calor y recorridos.
- Filtrado de staff.
- Re-identificación efímera y multi-cámara, sujeta a validación de privacidad.
- API, exportaciones e integraciones empresariales.

La IA generativa y MCP se consideran una capa de consulta sobre métricas ya
validadas, no un sustituto del pipeline de visión.

## Modelos de despliegue

Los tres modelos compartirán el mismo control plane, esquema de eventos,
versionado de modelos, observabilidad y política de privacidad.

### 1. Local / edge — disponible primero

El procesamiento ocurre en Maja y solo se sincronizan eventos, métricas y salud.
Las credenciales RTSP y los identificadores efímeros permanecen locales.

- Raspberry o equipo ligero: conector, configuración, buffer y una carga de
  inferencia limitada.
- Nodo local x86/NPU/GPU: varias cámaras o analíticas continuas.
- Debe continuar trabajando sin internet y sincronizar al recuperarse.

### 2. Centralizado — planificado

Un servidor o cluster del cliente procesa cámaras de varias tiendas mediante una
red corporativa, VPN o VMS central como HikCentral.

- Evita un servidor de inferencia por tienda.
- Conviene cuando el video ya está centralizado.
- Requiere dimensionar GPU, sesiones del VMS, redundancia y ancho de banda entre
  tiendas y datacenter.
- El agente local puede mantenerse como conector y mecanismo de continuidad.

### 3. Cloud — planificado después del centralizado

La inferencia corre en infraestructura administrada por CameraApp o en la nube
del cliente.

- Facilita despliegues sin servidor físico de inferencia en tienda.
- Exige acceso seguro al stream, cálculo de ancho de banda, aislamiento por
  cliente, cifrado y una definición explícita de dónde se procesa video crudo.
- Debe ofrecer regiones, retención configurable y un modo donde solo persistan
  metadatos.
- No se ofrecerá como opción predeterminada hasta validar costo, latencia y
  cumplimiento de privacidad.

## Decisiones pendientes

- Seleccionar detector, tracker y runtime con licencia comercial compatible.
- Definir hardware mínimo por cantidad y resolución de cámaras.
- Diseñar tablas separadas para tráfico, observaciones demográficas y corridas de
  validación.
- Implementar configuración remota de líneas y zonas.
- Definir límites de retención de artefactos de auditoría y Re-ID.
- Probar acceso centralizado mediante HikCentral/VMS antes de prometer
  compatibilidad comercial.

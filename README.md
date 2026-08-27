# IMERG para QGIS

![IMERG](imerg.gif)

[![QGIS](https://img.shields.io/badge/QGIS-3.40-589632?logo=qgis&logoColor=white)](https://qgis.org)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![NASA IMERG](https://img.shields.io/badge/NASA-IMERG%20Early-0B3D91?logo=nasa&logoColor=white)](https://gpm.nasa.gov/data/imerg)
[![GIBS](https://img.shields.io/badge/GIBS-30%20min-1B4F72)](https://www.earthdata.nasa.gov/learn/find-data/near-real-time/gibs)
[![GDAL](https://img.shields.io/badge/GDAL-NumPy-5CA81E)](https://gdal.org)
[![ffmpeg](https://img.shields.io/badge/ffmpeg-optional-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Scripts de QGIS 3.40 para descargar lluvia **NASA IMERG Early** (GIBS, cada 30 min), animarla y calcular el **acumulado en un punto** con un clic.

Pensado para eventos (inundaciones, monitoreo) sobre un país. Por defecto: **Nepal** y las **últimas 72 horas UTC**.    Cercano tiempo real (near-realtime) 4 horas de atraso contra hora actual.

## Scripts

Solo hay **dos** scripts. Incluyen descarga, animación/video y acumulado al clic.

| Script | Qué hace |
| --- | --- |
| [`IMERG pais, fechas y video.py`](IMERG%20pais%2C%20fechas%20y%20video.py) | Descarga GeoTIFF, carga capas, frontera, OSM, PNG y video MP4/GIF |
| [`IMERG acumulado clic.py`](IMERG%20acumulado%20clic.py) | Clic dentro del país → acumulado de lluvia del periodo (mm) |

## Requisitos

- **QGIS 3.40** o compatible (3.28+ suele funcionar)
- Windows, macOS o Linux
- Internet (NASA GIBS + polígono de países)
- **GDAL** y **NumPy** (vienen con QGIS)
- **ffmpeg** solo si quieres MP4 (el GIF no lo necesita)

No hace falta cuenta NASA Earthdata para GIBS WMS.

## Instalación

### 1. Clonar o copiar el repositorio

```bash
git clone <URL_DEL_REPO>
```

O descarga el ZIP y descomprime. Deja los dos scripts IMERG en la misma carpeta.

### 2. Añadir los scripts a QGIS

**Opción A — carpeta de scripts (recomendada)**

1. QGIS → **Configuración → Opciones → Procesos**
2. Proveedores → **Scripts**
3. En **Carpeta de scripts**, añade la carpeta del repositorio
4. Acepta y reabre la **Caja de herramientas**

**Opción B — un script cada vez**

1. Caja de herramientas → **Scripts → Añadir script a la caja de herramientas**
2. Elige `IMERG pais, fechas y video.py`
3. Repite con `IMERG acumulado clic.py`

Deben aparecer en:

**Caja de herramientas → Scripts → NASA IMERG**

- IMERG país, fechas y video
- IMERG acumulado al clic

### 3. ffmpeg (solo para MP4)

El script busca, en este orden:

1. `ffmpeg` en el `PATH`
2. `C:\ffmpeg\bin\ffmpeg.exe`
3. `C:\Program Files\ffmpeg\bin\ffmpeg.exe`

En Windows, lo habitual:

1. Descarga un build estático: [https://www.gyan.dev/ffmpeg/builds/](https://www.gyan.dev/ffmpeg/builds/)
2. Extrae y deja el ejecutable en `C:\ffmpeg\bin\ffmpeg.exe`

Sin ffmpeg, la descarga y los PNG siguen funcionando; el MP4 se omite.

---

## Uso 1 — Descargar IMERG y generar video

**Caja de herramientas → NASA IMERG → IMERG país, fechas y video**

### Parámetros

| Parámetro | Descripción | Defecto |
| --- | --- | --- |
| País | Lista desde GeoJSON de países | Nepal |
| Inicio / Fin | Rango **UTC**, alineado a 30 min | Últimas 72 h |
| Buffer | Km alrededor del país (recuadro de descarga) | 200 |
| Carpeta del proyecto | Donde se crea `IMERG_Pais_fechas/` | `~/IMERG_NASA` |
| OpenStreetMap | Basemap | Sí |
| Frontera + buffer | Capa vectorial del país | Sí (hace falta para el clic) |
| FPS | Velocidad del video | 4 |
| Ancho (px) | Ancho de PNG/MP4 | 1280 |
| Exportar PNG / GIF / MP4 | Salidas de animación | PNG y MP4 sí; GIF no |

El máximo es **720 frames** (~15 días a 30 min).

### Salida en disco

```
<carpeta>/
  IMERG_Nepal_YYYYMMDD_YYYYMMDD/
    frames/          GeoTIFF IMERG_YYYYMMDD_HHMM.tif
    png/             secuencia para el video
    IMERG_Nepal.mp4
    IMERG_Nepal.gif  (si se pidió)
```

### En QGIS

- Grupo **IMERG NASA - \<país\>**
- Controlador temporal listo para **Play**
- Capas **Frontera \<país\>** y **\<país\> + 200 km**
- OSM al fondo

### Consola de Python (opcional)

```python
exec(open(r"RUTA\IMERG pais, fechas y video.py", encoding="utf-8").read())
```

Usa el país y las 72 h por defecto. Para otro país o fechas, usa el diálogo de Procesos.

---

## Uso 2 — Acumulado de lluvia al clic

Requisito: haber corrido el script 1 **con frontera del país** y tener los rasters IMERG en el proyecto.

**Caja de herramientas → NASA IMERG → IMERG acumulado al clic**

1. El cursor pasa a cruz
2. Pulsa **dentro de la frontera del país** (no en el buffer)
3. Espera el recuento (todos los frames del periodo)
4. Verás el total en **mm**, un punto etiquetado y la capa `Acumulado lluvia IMERG`
5. Puedes pulsar varios puntos; **Esc** sale de la herramienta

### Consola

```python
exec(open(r"RUTA\IMERG acumulado clic.py", encoding="utf-8").read())
```

### Si quedan cruces en el mapa

No son una capa. En la consola de Python:

```python
from qgis.utils import iface
from qgis.gui import QgsVertexMarker
canvas = iface.mapCanvas()
for item in list(canvas.scene().items()):
    if isinstance(item, QgsVertexMarker):
        item.hide()
        canvas.scene().removeItem(item)
canvas.refresh()
```

(El script actual ya no dibuja esas cruces; el punto queda en la capa de acumulado.)

---

## Cómo se calcula la precipitación

IMERG aquí es **tasa cada 30 minutos** (mm/h), no un acumulado nativo.

**Lluvia de una hora** = promedio de los dos valores de esa hora:

```
08:00 → 15 mm/h
08:30 → 40 mm/h
hora  = (15 + 40) / 2 = 27.5 mm
```

**Acumulado del periodo** = suma de esos promedios horarios.

Si una hora solo tiene un raster, se usa ese valor.

Los GeoTIFF de GIBS son **RGB de visualización**. El color se traduce a mm/h con la paleta NASA *GPM Precipitation Rate* (no son valores científicos HDF5).

### Escala de color (interpretada)

| Color | Intensidad |
| --- | ---: |
| Verde | ≈ 0.1–0.5 mm/h |
| Amarillo | ≈ 1 mm/h |
| Naranja | ≈ 2–5 mm/h |
| Rojo brillante | ≈ 8 mm/h |
| Rojo oscuro | **15, 25, 35, 45, 55, 65, 75, 85, 95 mm/h** (más oscuro = más lluvia) |

El amarillo/rojo de GIBS **no** es la barra clásica de 20/50 mm/h.

---

## Datos y limitaciones

- Fuente: [NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/) capa `IMERG_Precipitation_Rate_30min_v7_NRT` (IMERG Early v7, 30 min)
- Resolución nativa IMERG: **0.1°** (~11 km), **30 min** (no hay producto IMERG cada 15 min; :15 y :45 son el instante representativo de cada media hora)
- Útil para ver el episodio y un acumulado aproximado en un punto; no sustituye estaciones ni el HDF5/GeoTIFF científico de GPM PPS
- Hace falta red; GIBS a veces falla un frame (queda registrado y se sigue)

Cita sugerida: Huffman et al., NASA GPM IMERG; visualización GIBS / Worldview.

---

## Problemas frecuentes

| Síntoma | Qué probar |
| --- | --- |
| No aparece el grupo NASA IMERG | Añade la **carpeta** del repo como carpeta de scripts y recarga Procesos |
| No hay MP4 | Instala ffmpeg en `C:\ffmpeg\bin\ffmpeg.exe` o el PATH |
| «No hay rasters IMERG» al clic | Ejecuta antes el script de descarga |
| «Fuera del área del país» | Pulsa dentro de **Frontera …**, no en el buffer |
| «No está la capa de frontera» | En la descarga, deja activado **Añadir frontera del país** |
| Video sin lluvia / OSM tapa todo | OSM debe quedar al fondo; el script lo mueve solo |
| Demasiados frames | Acorta el rango (máx. 720 × 30 min) |

---

## Licencia

Los **scripts** de este repositorio se publican bajo la [licencia MIT](LICENSE) (ABC Geomática Agrícola SRL, 2026).

Los productos **IMERG / GIBS** son de NASA / EOSDIS y se rigen por sus propias condiciones de uso. Esta licencia no cubre esos datos.

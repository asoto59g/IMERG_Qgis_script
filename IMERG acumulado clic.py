# ============================================================
# IMERG — acumulado de lluvia al clic
# QGIS 3.40 Processing
#
# Con los GeoTIFF cargados por «IMERG país, fechas y video»,
# haz clic en un punto DENTRO del país. El script lee la celda
# de todos los rasters IMERG, convierte el color GIBS a mm/h
# con la paleta logarítmica NASA (verde ≈ 0.1, amarillo ≈ 1,
# rojo ≈ 8). El rojo oscuro se interpola en pasos finos:
# 15, 25, 35, 45, 55, 65, 75, 85, 95 mm/h.
#
#     precipitación de una hora = promedio de esa hora
#     ej. 08:00 = 15 mm,  08:30 = 40 mm  →  27.5 mm
#     acumulado (mm) = Σ promedios horarios
#
# (se registra cada 30 minutos; no se suman los dos valores crudos).
#
# Cómo usarlo
# 1. Caja de herramientas → Scripts → NASA IMERG
#    → IMERG acumulado al clic
# 2. O desde la consola de Python de QGIS:
#    exec(open(r"RUTA\IMERG acumulado clic.py", encoding="utf-8").read())
# ============================================================

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QVariant,
    Qt,
)
from qgis.PyQt.QtGui import QColor, QCursor, QFont
from qgis.PyQt.QtWidgets import QMessageBox, QProgressDialog

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProject,
    QgsRaster,
    QgsRasterLayer,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker

import os
import re
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone


INTERVALO_MINUTOS = 30
CAPA_PUNTOS = "Acumulado lluvia IMERG"
PATRON_TIF = re.compile(r"IMERG_(\d{8})_(\d{4})\.tif$", re.IGNORECASE)
PATRON_NOMBRE = re.compile(
    r"IMERG\s+(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s+UTC",
    re.IGNORECASE,
)

# Paleta oficial GIBS «GPM_Precipitation_Rate» (leyenda de
# IMERG_Precipitation_Rate_30min_v7_NRT). 110 bins logarítmicos
# de 0.1 a ≥53 mm/h, lluvia (verde→rojo) y nieve (cian→violeta).
_RAIN_RGB = (
    "0,118,78 0,120,75 0,123,72 0,126,69 0,129,66 0,131,62 0,134,58 "
    "0,137,54 0,140,50 0,142,46 0,145,41 0,148,36 0,151,31 0,153,26 "
    "0,156,20 0,159,15 0,162,9 0,165,3 3,167,0 9,170,0 16,173,0 "
    "23,176,0 30,178,0 38,181,0 45,184,0 53,187,0 61,189,0 69,192,0 "
    "78,195,0 86,198,0 95,200,0 104,203,0 114,206,0 123,209,0 "
    "133,212,0 143,214,0 153,217,0 163,220,0 174,223,0 184,225,0 "
    "195,228,0 207,231,0 218,234,0 230,236,0 239,237,0 242,230,0 "
    "245,224,0 247,217,0 250,210,0 253,202,0 255,194,1 255,185,4 "
    "255,176,6 255,168,9 255,160,12 255,152,15 255,144,17 255,136,20 "
    "255,129,23 255,122,26 255,115,28 255,108,31 255,101,34 255,95,37 "
    "255,89,39 255,83,42 255,77,45 255,71,48 255,66,51 255,52,44 "
    "255,38,38 255,32,32 255,26,26 255,19,19 255,13,13 255,7,7 "
    "255,1,1 250,0,0 243,0,0 237,0,0 231,0,0 225,0,0 218,0,0 "
    "212,0,0 206,0,0 200,0,0 194,0,0 187,0,0 181,0,0 175,0,0 "
    "169,0,0 162,0,0 156,0,0 150,0,0 144,0,0 138,0,0 131,0,0 "
    "125,0,0 119,0,0 113,0,0 106,0,0 100,0,0 94,0,0 88,0,0 "
    "82,0,0 75,0,0 69,0,0 63,0,0 57,0,0 51,0,0"
)
_SNOW_RGB = (
    "177,250,238 174,250,239 170,249,239 167,248,240 164,248,241 "
    "161,247,242 158,246,243 155,245,244 152,244,244 149,241,243 "
    "146,238,242 143,235,241 140,231,240 137,228,239 134,224,238 "
    "132,221,237 129,217,236 126,213,234 124,209,233 121,205,232 "
    "118,201,230 116,197,229 113,193,228 111,189,226 109,184,225 "
    "106,180,223 104,176,221 102,171,220 99,167,218 97,162,216 "
    "95,158,215 93,153,213 91,149,211 89,144,209 87,140,207 "
    "85,135,205 83,131,203 81,126,201 79,122,199 77,117,197 "
    "79,115,196 77,112,197 76,109,198 75,106,199 74,102,201 "
    "73,99,202 72,95,203 70,92,204 69,88,205 68,84,206 67,80,208 "
    "66,76,209 64,72,210 63,68,211 62,64,212 63,61,213 65,60,215 "
    "67,59,216 69,57,217 72,56,218 74,55,219 77,54,220 80,53,222 "
    "83,52,223 86,50,224 89,49,225 92,48,226 95,47,228 99,46,229 "
    "102,45,230 106,43,231 106,37,232 106,31,232 107,25,233 "
    "109,20,233 111,18,229 112,16,225 114,15,221 116,13,217 "
    "117,12,213 119,11,209 120,10,204 121,10,199 122,10,193 "
    "122,9,188 123,9,183 123,9,178 123,9,172 122,8,167 122,8,162 "
    "121,8,157 120,8,152 119,7,146 118,7,141 116,7,136 114,6,131 "
    "112,6,125 110,6,120 108,6,115 105,5,110 102,5,105 99,5,99 "
    "94,4,93 89,4,86 84,4,79 79,4,72 73,3,66 68,3,60 63,3,54 "
    "58,3,48"
)

# Rojo oscuro GIBS: el canal R baja de ~181 a ~45 mientras G y B ≈ 0.
# NASA comprime 15–53 mm/h ahí; se estira a una escala fina 15–95.
ROJO_OSCURO_R = (181, 156, 130, 113, 90, 75, 63, 57, 45)
ROJO_OSCURO_MMH = (15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0, 85.0, 95.0)
_CMAP_EXACT = None
_RAIN_LIST = None
_SNOW_LIST = None
_TOOL_REF = []
_MARCADORES = []


def _no_threading_flag():
    flag = getattr(Qgis, "ProcessingAlgorithmFlag", None)
    if flag is not None and hasattr(flag, "NoThreading"):
        return flag.NoThreading
    return QgsProcessingAlgorithm.FlagNoThreading


def _iface():
    try:
        from qgis.utils import iface
        return iface
    except Exception:
        return None


def _parent_window():
    iface = _iface()
    if iface is None:
        return None
    return iface.mainWindow()


def _qt_left():
    return getattr(
        Qt,
        "LeftButton",
        getattr(getattr(Qt, "MouseButton", Qt), "LeftButton"),
    )


def _qt_cross():
    return getattr(
        Qt,
        "CrossCursor",
        getattr(getattr(Qt, "CursorShape", Qt), "CrossCursor"),
    )


def _qt_escape():
    return getattr(
        Qt,
        "Key_Escape",
        getattr(getattr(Qt, "Key", Qt), "Key_Escape"),
    )


def _identify_value():
    fmt = getattr(QgsRaster, "IdentifyFormatValue", None)
    if fmt is not None:
        return fmt
    inner = getattr(QgsRaster, "IdentifyFormat", None)
    if inner is not None and hasattr(inner, "Value"):
        return inner.Value
    return 1


def _icon_cross():
    return getattr(
        QgsVertexMarker,
        "ICON_CROSS",
        getattr(getattr(QgsVertexMarker, "IconType", QgsVertexMarker), "ICON_CROSS"),
    )


def _parse_rgb_pack(texto):
    out = []
    for tok in texto.split():
        partes = tok.split(",")
        if len(partes) != 3:
            continue
        out.append((int(partes[0]), int(partes[1]), int(partes[2])))
    return out


def _mmh_bin(indice):
    """Borde inferior del bin log (40 pasos por década) a partir de 0.1 mm/h."""
    return 0.1 * (10.0 ** (min(indice, 109) / 40.0))


def colormap_imerg():
    """Paleta GIBS GPM Precipitation Rate: lluvia (verde→rojo) y nieve (cian→violeta)."""
    global _CMAP_EXACT, _RAIN_LIST, _SNOW_LIST
    if _CMAP_EXACT is not None:
        return _CMAP_EXACT, _RAIN_LIST, _SNOW_LIST

    exact = {}
    rain = []
    snow = []
    for pack, destino in ((_RAIN_RGB, rain), (_SNOW_RGB, snow)):
        colores = _parse_rgb_pack(pack)
        for i, rgb in enumerate(colores):
            mmh = _mmh_bin(i)
            exact[rgb] = mmh
            destino.append((rgb, mmh))
    exact[(191, 191, 191)] = 0.0
    exact[(128, 128, 128)] = 0.0
    _CMAP_EXACT = exact
    _RAIN_LIST = rain
    _SNOW_LIST = snow
    return exact, rain, snow


def _dist2(p, q):
    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2


def _brillo(rgb):
    return max(rgb[0], rgb[1], rgb[2])


def _interp_log(v0, v1, t):
    v0 = max(float(v0), 1e-6)
    v1 = max(float(v1), 1e-6)
    return math.exp((1.0 - t) * math.log(v0) + t * math.log(v1))


def _proyectar_rampa(pixel, rampa):
    """
    Proyecta el RGB sobre la polilínea de la paleta e interpola mm/h en log.
    Escala más fina que saltar al color más cercano.
    """
    if not rampa:
        return 0.0, 1e18, (0, 0, 0)
    mejor_d = 1e18
    mejor_mmh = 0.0
    mejor_rgb = rampa[0][0]
    for rgb, mmh in rampa:
        d = _dist2(pixel, rgb)
        if d < mejor_d:
            mejor_d = d
            mejor_mmh = mmh
            mejor_rgb = rgb
    for i in range(len(rampa) - 1):
        a, va = rampa[i]
        b, vb = rampa[i + 1]
        vx = b[0] - a[0]
        vy = b[1] - a[1]
        vz = b[2] - a[2]
        den = vx * vx + vy * vy + vz * vz
        if den < 1:
            continue
        t = (
            (pixel[0] - a[0]) * vx
            + (pixel[1] - a[1]) * vy
            + (pixel[2] - a[2]) * vz
        ) / float(den)
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        qx = a[0] + t * vx
        qy = a[1] + t * vy
        qz = a[2] + t * vz
        d = (pixel[0] - qx) ** 2 + (pixel[1] - qy) ** 2 + (pixel[2] - qz) ** 2
        if d < mejor_d:
            mejor_d = d
            mejor_mmh = _interp_log(va, vb, t)
            mejor_rgb = (qx, qy, qz)
    return mejor_mmh, mejor_d, mejor_rgb


def clave_hora(dt):
    """Agrupa :00 y :30 de la misma hora (UTC)."""
    return dt.replace(minute=0, second=0, microsecond=0)


def precipitacion_promedio_horario(muestras):
    """
    Precipitación de una hora = promedio de las lecturas de 30 min de esa hora.

    Ej. 08:00 = 15 mm, 08:30 = 40 mm → 27.5 mm esa hora.
    Acumulado = suma de los promedios horarios.
    Si una hora solo tiene un raster, se usa ese valor.
    Sin marca de tiempo: se promedian de dos en dos (orden de carga).
    """
    por_hora = defaultdict(list)
    sin_tiempo = []
    for tiempo, valor in muestras:
        try:
            v = float(valor)
        except (TypeError, ValueError):
            continue
        if tiempo is None:
            sin_tiempo.append(v)
            continue
        por_hora[clave_hora(tiempo)].append(v)

    horas = []
    for clave in sorted(por_hora):
        vals = por_hora[clave]
        horas.append((clave, sum(vals) / float(len(vals)), len(vals)))
    for i in range(0, len(sin_tiempo), 2):
        grupo = sin_tiempo[i:i + 2]
        horas.append((None, sum(grupo) / float(len(grupo)), len(grupo)))
    total = sum(item[1] for item in horas)
    return total, horas


def mmh_rojo_oscuro_fino(r, g, b):
    """
    Escala fina del rojo oscuro: 15, 25, 35, 45, 55, 65, 75, 85, 95 mm/h.
    Más oscuro (R más bajo, G y B ~ 0) → más precipitación.
    """
    if g > 28 or b > 28:
        return None
    if r < 38 or r > 210:
        return None
    if r >= ROJO_OSCURO_R[0]:
        return ROJO_OSCURO_MMH[0]
    if r <= ROJO_OSCURO_R[-1]:
        return ROJO_OSCURO_MMH[-1]
    for i in range(len(ROJO_OSCURO_R) - 1):
        r0 = ROJO_OSCURO_R[i]
        r1 = ROJO_OSCURO_R[i + 1]
        if r1 <= r <= r0:
            t = (r0 - r) / float(r0 - r1)
            m0 = ROJO_OSCURO_MMH[i]
            m1 = ROJO_OSCURO_MMH[i + 1]
            return m0 + t * (m1 - m0)
    return None


def rgb_a_mmh(r, g, b, a=None):
    """
    Convierte un píxel GIBS (RGB 0-255) a mm/h.

    Verde ≈ 0.1, amarillo ≈ 1, naranja ≈ 2–5, rojo brillante ≈ 8 mm/h.
    Rojo oscuro (granate): escala fina 15, 25, 35, 45, 55, 65, 75, 85, 95 mm/h
    interpolando el canal R (más oscuro = más lluvia).
    """
    if r is None or g is None or b is None:
        return 0.0
    try:
        r = int(round(float(r)))
        g = int(round(float(g)))
        b = int(round(float(b)))
    except (TypeError, ValueError):
        return 0.0
    if a is not None:
        try:
            if float(a) < 16:
                return 0.0
        except (TypeError, ValueError):
            pass
    if r >= 230 and g >= 230 and b >= 230:
        return 0.0
    if r <= 20 and g <= 20 and b <= 20:
        return 0.0

    fino = mmh_rojo_oscuro_fino(r, g, b)
    if fino is not None:
        return fino

    exact, rain, snow = colormap_imerg()
    pixel = (r, g, b)
    if pixel in exact:
        return exact[pixel]

    mejor_e = 1e18
    val_e = 0.0
    rgb_e = pixel
    for rgb, mmh in exact.items():
        d = _dist2(pixel, rgb)
        if d < mejor_e:
            mejor_e = d
            val_e = mmh
            rgb_e = rgb
    if mejor_e <= 12:
        return val_e

    mx = max(r, g, b)
    if mx <= 40:
        return 0.0

    mmh_r, d_r, rgb_r = _proyectar_rampa(pixel, rain)
    mmh_s, d_s, rgb_s = _proyectar_rampa(pixel, snow)
    if d_r <= d_s:
        mmh, d, rgb_p = mmh_r, d_r, rgb_r
    else:
        mmh, d, rgb_p = mmh_s, d_s, rgb_s
    if d > 40 * 40:
        return 0.0

    bc = _brillo(rgb_p)
    if bc >= 25 and mx < bc * 0.65:
        mmh = mmh * (mx / float(bc))
    return mmh


def transformar_punto(pt, crs_origen, crs_destino, project):
    if not crs_origen.isValid() or not crs_destino.isValid():
        return pt
    if crs_origen == crs_destino:
        return QgsPointXY(pt)
    xform = QgsCoordinateTransform(
        crs_origen,
        crs_destino,
        project.transformContext(),
    )
    return xform.transform(pt)


def es_capa_imerg(lyr):
    if not isinstance(lyr, QgsRasterLayer) or not lyr.isValid():
        return False
    proveedor = (lyr.providerType() or "").lower()
    if proveedor in ("wms", "xyz", "arcgismapserver"):
        return False
    nombre = lyr.name() or ""
    fuente = (lyr.source() or "").replace("\\", "/")
    base = os.path.basename(fuente)
    if nombre.startswith("IMERG"):
        return True
    if PATRON_TIF.match(base):
        return True
    if lyr.customProperty("IMERG/UTC") or lyr.customProperty("IMERG/start"):
        return True
    return False


def tiempo_capa(lyr):
    utc = lyr.customProperty("IMERG/UTC")
    if utc:
        for fmt in ("%Y-%m-%d %H:%M UTC", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(str(utc).replace("+00:00", ""), fmt.replace("%z", ""))
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    inicio = lyr.customProperty("IMERG/start")
    if inicio:
        try:
            texto = str(inicio).replace("Z", "+00:00")
            dt = datetime.fromisoformat(texto)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    base = os.path.basename((lyr.source() or "").replace("\\", "/"))
    m = PATRON_TIF.match(base)
    if m:
        return datetime.strptime(
            m.group(1) + m.group(2),
            "%Y%m%d%H%M",
        ).replace(tzinfo=timezone.utc)
    m = PATRON_NOMBRE.search(lyr.name() or "")
    if m:
        dia, mes, hora, minuto = m.groups()
        anio = datetime.now(timezone.utc).year
        try:
            return datetime(
                anio, int(mes), int(dia), int(hora), int(minuto),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None
    return None


def listar_imerg(project=None):
    if project is None:
        project = QgsProject.instance()
    capas = []
    for lyr in project.mapLayers().values():
        if es_capa_imerg(lyr):
            capas.append((tiempo_capa(lyr), lyr))
    capas.sort(key=lambda x: (x[0] is None, x[0] or datetime.min.replace(tzinfo=timezone.utc)))
    return capas


def capa_frontera(project=None):
    if project is None:
        project = QgsProject.instance()
    candidatas = []
    for lyr in project.mapLayers().values():
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            continue
        tipo_poly = getattr(QgsWkbTypes, "PolygonGeometry", 2)
        if lyr.geometryType() != tipo_poly:
            continue
        nombre = lyr.name() or ""
        if nombre.startswith("Frontera "):
            return lyr
        if "+ " in nombre and "km" in nombre.lower():
            continue
        candidatas.append(lyr)
    return None


def nombre_pais_frontera(frontera):
    nombre = (frontera.name() or "") if frontera else ""
    if nombre.startswith("Frontera "):
        return nombre[len("Frontera "):].strip()
    return nombre or "país"


def punto_en_poligono(pt_map, crs_map, capa, project):
    if capa is None or not capa.isValid():
        return False
    pt = transformar_punto(pt_map, crs_map, capa.crs(), project)
    geom_pt = QgsGeometry.fromPointXY(pt)
    for feat in capa.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if geom.contains(geom_pt) or geom.intersects(geom_pt):
            return True
    return False


def muestrear_tasa(raster, pt_map, crs_map, project):
    """Devuelve tasa mm/h en el punto, o None si no se pudo leer."""
    pt = transformar_punto(pt_map, crs_map, raster.crs(), project)
    extent = raster.extent()
    if not extent.contains(pt):
        return None
    provider = raster.dataProvider()
    if provider is None:
        return None
    ident = provider.identify(pt, _identify_value())
    if ident is None or not ident.isValid():
        return None
    res = ident.results() or {}
    if raster.bandCount() >= 3:
        r = res.get(1)
        g = res.get(2)
        b = res.get(3)
        a = res.get(4)
        if r is None and g is None and b is None:
            return None
        return rgb_a_mmh(r, g, b, a)
    val = res.get(1)
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return 0.0
    return v


def asegurar_capa_puntos(project=None):
    if project is None:
        project = QgsProject.instance()
    for lyr in project.mapLayers().values():
        if (lyr.name() or "") == CAPA_PUNTOS and isinstance(lyr, QgsVectorLayer):
            nombres = [f.name() for f in lyr.fields()]
            if "n_horas" not in nombres:
                lyr.dataProvider().addAttributes([QgsField("n_horas", QVariant.Int)])
                lyr.updateFields()
            return lyr

    capa = QgsVectorLayer("Point?crs=EPSG:4326", CAPA_PUNTOS, "memory")
    prov = capa.dataProvider()
    prov.addAttributes([
        QgsField("acumulado_mm", QVariant.Double),
        QgsField("lon", QVariant.Double),
        QgsField("lat", QVariant.Double),
        QgsField("n_rasters", QVariant.Int),
        QgsField("n_lluvia", QVariant.Int),
        QgsField("n_horas", QVariant.Int),
        QgsField("tasa_max", QVariant.Double),
        QgsField("inicio_utc", QVariant.String),
        QgsField("fin_utc", QVariant.String),
        QgsField("pais", QVariant.String),
    ])
    capa.updateFields()

    symbol = QgsMarkerSymbol.createSimple({
        "name": "circle",
        "color": "0,90,180,220",
        "outline_color": "255,255,255,255",
        "outline_width": "0.6",
        "size": "3.2",
    })
    capa.setRenderer(QgsSingleSymbolRenderer(symbol))

    texto = QgsTextFormat()
    texto.setSize(10)
    texto.setColor(QColor(10, 30, 70))
    font = QFont()
    font.setBold(True)
    texto.setFont(font)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.2)
    buf.setColor(QColor(255, 255, 255))
    texto.setBuffer(buf)

    pal = QgsPalLayerSettings()
    pal.fieldName = """format_number("acumulado_mm", 1) || ' mm'"""
    if hasattr(pal, "isExpression"):
        pal.isExpression = True
    pal.enabled = True
    try:
        pal.setFormat(texto)
    except Exception:
        pass
    try:
        capa.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        capa.setLabelsEnabled(True)
    except Exception:
        pass

    project.addMapLayer(capa, True)
    return capa


def agregar_punto_resultado(capa, lon, lat, total, n, n_lluvia, n_horas, tasa_max, t0, t1, pais):
    feat = QgsFeature(capa.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
    valores = {
        "acumulado_mm": round(total, 2),
        "lon": round(lon, 5),
        "lat": round(lat, 5),
        "n_rasters": int(n),
        "n_lluvia": int(n_lluvia),
        "n_horas": int(n_horas),
        "tasa_max": round(tasa_max, 2) if tasa_max is not None else None,
        "inicio_utc": t0,
        "fin_utc": t1,
        "pais": pais or "",
    }
    for nombre, valor in valores.items():
        idx = capa.fields().indexOf(nombre)
        if idx >= 0:
            feat.setAttribute(idx, valor)
    capa.dataProvider().addFeatures([feat])
    capa.updateExtents()
    capa.triggerRepaint()


def acumular_en_punto(pt_map, crs_map, feedback=None):
    project = QgsProject.instance()
    capas = listar_imerg(project)
    if not capas:
        raise QgsProcessingException(
            "No hay rasters IMERG en el proyecto. "
            "Ejecuta primero «IMERG país, fechas y video»."
        )

    frontera = capa_frontera(project)
    pais = nombre_pais_frontera(frontera)
    if frontera is None:
        raise QgsProcessingException(
            "No se encontró la capa «Frontera …». "
            "Carga IMERG con la opción de frontera del país."
        )
    if not punto_en_poligono(pt_map, crs_map, frontera, project):
        raise QgsProcessingException(
            "El clic está fuera del área de {}. "
            "Pulsa dentro de la frontera del país.".format(pais)
        )

    progress = None
    parent = _parent_window()
    if feedback is None:
        progress = QProgressDialog(
            "Promediando precipitacion por hora…",
            "Cancelar",
            0,
            len(capas),
            parent,
        )
        progress.setWindowTitle("Acumulado IMERG")
        progress.setMinimumDuration(0)
        progress.setValue(0)

    n_ok = 0
    n_lluvia = 0
    tasa_max = 0.0
    muestras = []

    try:
        for i, (tiempo, raster) in enumerate(capas):
            if progress is not None:
                progress.setValue(i)
                if progress.wasCanceled():
                    raise QgsProcessingException("Cálculo cancelado.")
            if feedback is not None and feedback.isCanceled():
                raise QgsProcessingException("Cálculo cancelado.")

            tasa = muestrear_tasa(raster, pt_map, crs_map, project)
            if tasa is None:
                continue
            n_ok += 1
            if tasa > 0:
                n_lluvia += 1
            if tasa > tasa_max:
                tasa_max = tasa
            muestras.append((tiempo, tasa))
    finally:
        if progress is not None:
            progress.setValue(len(capas))
            progress.close()

    if n_ok == 0:
        raise QgsProcessingException(
            "No se pudo leer ningún raster IMERG en ese punto."
        )

    total, horas = precipitacion_promedio_horario(muestras)
    tiempos = [t for t, _ in muestras if t is not None]

    t0 = min(tiempos) if tiempos else None
    t1 = max(tiempos) if tiempos else None
    t1_fin = t1 + timedelta(minutes=INTERVALO_MINUTOS) if t1 is not None else None

    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    pt_geo = transformar_punto(pt_map, crs_map, wgs84, project)

    def fmt(dt):
        if dt is None:
            return "—"
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    return {
        "total_mm": total,
        "lon": pt_geo.x(),
        "lat": pt_geo.y(),
        "n_rasters": n_ok,
        "n_lluvia": n_lluvia,
        "n_horas": len(horas),
        "tasa_max": tasa_max,
        "inicio": fmt(t0),
        "fin": fmt(t1_fin),
        "pais": pais,
        "n_capas": len(capas),
    }


def texto_resultado(r):
    return (
        "Acumulado de lluvia:  {total:.1f} mm\n\n"
        "Punto:  {lon:.5f} °E,  {lat:.5f} °N\n"
        "País:   {pais}\n"
        "Periodo: {inicio}  →  {fin}\n"
        "Rasters usados: {n_rasters} de {n_capas}  (cada 30 min)\n"
        "Horas promediadas: {n_horas}\n"
        "Intervalos con lluvia: {n_lluvia}\n"
        "Valor máximo de celda: {tasa_max:.1f} mm/h\n\n"
        "Escala (mm/h): verde ≈ 0.1–0.5   amarillo ≈ 1\n"
        "  naranja ≈ 2–5   rojo ≈ 8\n"
        "  rojo oscuro (fino): 15, 25, 35, 45, 55, 65, 75, 85, 95\n\n"
        "Fórmula: precipitación de cada hora = promedio de esa hora\n"
        "  ej. 08:00 = 15 mm,  08:30 = 40 mm  →  27.5 mm\n"
        "  acumulado = Σ promedios horarios"
    ).format(
        total=r["total_mm"],
        lon=r["lon"],
        lat=r["lat"],
        pais=r["pais"],
        inicio=r["inicio"],
        fin=r["fin"],
        n_rasters=r["n_rasters"],
        n_capas=r["n_capas"],
        n_horas=r["n_horas"],
        n_lluvia=r["n_lluvia"],
        tasa_max=r["tasa_max"],
    )


def limpiar_cruces(canvas=None):
    """Quita las cruces (QgsVertexMarker) del mapa. No son una capa."""
    if canvas is None:
        iface = _iface()
        canvas = iface.mapCanvas() if iface is not None else None
    for marker in list(_MARCADORES):
        try:
            marker.hide()
            if canvas is not None:
                canvas.scene().removeItem(marker)
        except Exception:
            pass
    _MARCADORES[:] = []
    if canvas is None:
        return 0
    n = 0
    for item in list(canvas.scene().items()):
        if isinstance(item, QgsVertexMarker) or item.__class__.__name__ == "QgsVertexMarker":
            try:
                item.hide()
                canvas.scene().removeItem(item)
                n += 1
            except Exception:
                pass
    canvas.refresh()
    return n


def presentar_resultado(r, pt_map, canvas):
    iface = _iface()
    parent = _parent_window()
    capa = asegurar_capa_puntos()
    agregar_punto_resultado(
        capa,
        r["lon"],
        r["lat"],
        r["total_mm"],
        r["n_rasters"],
        r["n_lluvia"],
        r["n_horas"],
        r["tasa_max"],
        r["inicio"],
        r["fin"],
        r["pais"],
    )

    if canvas is not None:
        canvas.refresh()

    msg = texto_resultado(r)
    print(msg)
    if iface is not None:
        iface.messageBar().pushMessage(
            "Acumulado IMERG",
            "{:.1f} mm".format(r["total_mm"]),
            Qgis.Success,
            10,
        )
    QMessageBox.information(parent, "Acumulado de lluvia IMERG", msg)


class MapToolAcumuladoImerg(QgsMapToolEmitPoint):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.setCursor(QCursor(_qt_cross()))

    def canvasReleaseEvent(self, event):
        if event.button() != _qt_left():
            return
        pt = self.toMapCoordinates(event.pos())
        crs_map = self.canvas.mapSettings().destinationCrs()
        parent = _parent_window()
        try:
            resultado = acumular_en_punto(pt, crs_map)
        except QgsProcessingException as e:
            QMessageBox.warning(parent, "IMERG acumulado", str(e))
            return
        except Exception as e:
            QMessageBox.critical(parent, "IMERG acumulado", str(e))
            return
        presentar_resultado(resultado, pt, self.canvas)

    def keyPressEvent(self, event):
        if event.key() == _qt_escape():
            self.canvas.unsetMapTool(self)

    def deactivate(self):
        super().deactivate()


def activar_herramienta():
    iface = _iface()
    if iface is None:
        raise QgsProcessingException(
            "Esta herramienta necesita la interfaz de QGIS (mapa)."
        )
    capas = listar_imerg()
    if not capas:
        raise QgsProcessingException(
            "No hay rasters IMERG cargados. "
            "Ejecuta primero «IMERG país, fechas y video»."
        )
    frontera = capa_frontera()
    if frontera is None:
        raise QgsProcessingException(
            "No está la capa de frontera del país. "
            "Vuelve a cargar IMERG con «Añadir frontera del país»."
        )

    canvas = iface.mapCanvas()
    limpiar_cruces(canvas)
    tool = MapToolAcumuladoImerg(canvas)
    _TOOL_REF[:] = [tool]
    canvas.setMapTool(tool)
    iface.messageBar().pushMessage(
        "IMERG acumulado",
        "Haz clic dentro de {} para calcular el total de lluvia (mm). "
        "Esc cancela la herramienta. {} rasters cargados.".format(
            nombre_pais_frontera(frontera),
            len(capas),
        ),
        Qgis.Info,
        12,
    )
    return len(capas)


class ImmergAcumuladoClicAlgorithm(QgsProcessingAlgorithm):

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return ImmergAcumuladoClicAlgorithm()

    def name(self):
        return "imerg_acumulado_clic"

    def displayName(self):
        return self.tr("IMERG acumulado al clic")

    def group(self):
        return self.tr("NASA IMERG")

    def groupId(self):
        return "nasa_imerg"

    def shortHelpString(self):
        return self.tr(
            "Activa una herramienta de clic sobre el mapa. "
            "Pulsa un punto DENTRO de la frontera del país: se lee la "
            "misma celda en todos los rasters IMERG cargados.\n\n"
            "El acumulado agrupa los rasters de 30 min por hora de reloj "
            "y promedia esa hora (no se suman los dos valores):\n"
            "  ej. 08:00 = 15 mm,  08:30 = 40 mm  →  27.5 mm esa hora\n"
            "  mm = Σ promedios horarios\n\n"
            "El total se muestra en mm, se anota en el mapa y se guarda "
            "en la capa «Acumulado lluvia IMERG».\n\n"
            "Rojo oscuro: escala fina 15, 25, 35, 45, 55, 65, 75, 85, 95 mm/h "
            "(más oscuro = más lluvia). Verde ≈ 0.1, amarillo ≈ 1, rojo ≈ 8 mm/h."
        )

    def flags(self):
        return _no_threading_flag()

    def initAlgorithm(self, config=None):
        return

    def processAlgorithm(self, parameters, context, feedback):
        n = activar_herramienta()
        feedback.pushInfo(
            "Herramienta activa. Haz clic dentro del país. "
            "Rasters IMERG: {}".format(n)
        )
        return {"rasters": n}


def run():
    parent = _parent_window()
    try:
        n = activar_herramienta()
    except Exception as e:
        QMessageBox.critical(parent, "Error IMERG acumulado", str(e))
        raise
    QMessageBox.information(
        parent,
        "IMERG acumulado al clic",
        "Herramienta lista.\n\n"
        "Haz clic con el cursor dentro del país para obtener "
        "el acumulado de lluvia del periodo (mm).\n\n"
        "Rasters IMERG: {}\n"
        "Fórmula: promedio de cada hora (08:00 y 08:30) → Σ horas = mm\n\n"
        "Esc para salir de la herramienta.".format(n),
    )
    return n


if __name__ == "__main__":
    run()

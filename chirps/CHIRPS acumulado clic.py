# ============================================================
# CHIRPS — acumulado de lluvia al clic
# QGIS 3.40 Processing
#
# Con los GeoTIFF cargados por «CHIRPS país, fechas y video»,
# haz clic DENTRO del país. Cada raster es un día en mm/día.
# El acumulado del periodo es la suma de esos valores diarios.
#
# Paleta (mm/día): 5, 10, 15, 25, 35, 50, 75, 100, 125, 150
# (más fracciones: 2.5, 7.5, 12.5, 20, 30, 40, 60, 90, 200).
#
# Caja de herramientas → Scripts → CHIRPS
#   → CHIRPS acumulado al clic
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
from collections import defaultdict
from datetime import datetime, timedelta, timezone


CAPA_PUNTOS = "Acumulado lluvia CHIRPS"
PATRON_TIF = re.compile(r"CHIRPS_(\d{8})\.tif$", re.IGNORECASE)
PATRON_NOMBRE = re.compile(
    r"CHIRPS\s+(\d{2})-(\d{2})-(\d{4})",
    re.IGNORECASE,
)
NODATA = -9999.0

RAMP_MM_DIA = [
    (0.1, 0, 100, 70),
    (2.5, 0, 140, 50),
    (5.0, 20, 180, 20),
    (7.5, 80, 200, 0),
    (10.0, 160, 220, 0),
    (12.5, 210, 230, 0),
    (15.0, 250, 210, 0),
    (20.0, 255, 170, 0),
    (25.0, 255, 130, 10),
    (30.0, 255, 90, 20),
    (35.0, 255, 55, 25),
    (40.0, 255, 30, 20),
    (50.0, 240, 0, 0),
    (60.0, 210, 0, 0),
    (75.0, 175, 0, 10),
    (90.0, 145, 0, 20),
    (100.0, 120, 0, 30),
    (125.0, 90, 0, 60),
    (150.0, 70, 0, 90),
    (200.0, 40, 0, 110),
]

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


def _dist2(p, q):
    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2


def rgb_a_mmdia(r, g, b, a=None):
    """Si el raster es RGB (visualización), aproxima mm/día con la paleta CHIRPS."""
    try:
        r = int(round(float(r)))
        g = int(round(float(g)))
        b = int(round(float(b)))
    except (TypeError, ValueError):
        return 0.0
    if a is not None:
        try:
            if float(a) <= 5:
                return 0.0
        except (TypeError, ValueError):
            pass
    if r <= 20 and g <= 20 and b <= 20:
        return 0.0
    if r >= 230 and g >= 230 and b >= 230:
        return 0.0
    pixel = (r, g, b)
    mejor_d = 1e18
    mejor_i = 0
    for i, (_mm, rr, gg, bb) in enumerate(RAMP_MM_DIA):
        d = _dist2(pixel, (rr, gg, bb))
        if d < mejor_d:
            mejor_d = d
            mejor_i = i
    if mejor_d > 80 ** 2:
        return 0.0
    if mejor_i == 0:
        return RAMP_MM_DIA[0][0]
    if mejor_i == len(RAMP_MM_DIA) - 1:
        return RAMP_MM_DIA[-1][0]
    mm0, r0, g0, b0 = RAMP_MM_DIA[mejor_i]
    # interpolar con el vecino más cercano en la rampa
    i2 = mejor_i + 1 if mejor_i + 1 < len(RAMP_MM_DIA) else mejor_i - 1
    mm1, r1, g1, b1 = RAMP_MM_DIA[i2]
    denom = _dist2((r0, g0, b0), (r1, g1, b1)) ** 0.5
    if denom < 1:
        return mm0
    t = ((_dist2(pixel, (r0, g0, b0)) ** 0.5) / denom)
    t = max(0.0, min(1.0, t))
    return mm0 + t * (mm1 - mm0)


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


def es_capa_chirps(lyr):
    if not isinstance(lyr, QgsRasterLayer) or not lyr.isValid():
        return False
    proveedor = (lyr.providerType() or "").lower()
    if proveedor in ("wms", "xyz", "arcgismapserver"):
        return False
    nombre = lyr.name() or ""
    fuente = (lyr.source() or "").replace("\\", "/")
    base = os.path.basename(fuente)
    if nombre.startswith("IMERG"):
        return False
    if nombre.startswith("CHIRPS"):
        return True
    if PATRON_TIF.match(base):
        return True
    if lyr.customProperty("CHIRPS/UTC") or lyr.customProperty("CHIRPS/start"):
        return True
    return False


def tiempo_capa(lyr):
    utc = lyr.customProperty("CHIRPS/UTC")
    if utc:
        for fmt in ("%Y-%m-%d UTC", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(str(utc).replace("+00:00", ""), fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    inicio = lyr.customProperty("CHIRPS/start")
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
        return datetime.strptime(m.group(1), "%Y%m%d").replace(
            tzinfo=timezone.utc
        )
    m = PATRON_NOMBRE.search(lyr.name() or "")
    if m:
        dia, mes, anio = m.groups()
        try:
            return datetime(
                int(anio), int(mes), int(dia), tzinfo=timezone.utc
            )
        except ValueError:
            return None
    return None


def listar_chirps(project=None):
    if project is None:
        project = QgsProject.instance()
    capas = []
    for lyr in project.mapLayers().values():
        if es_capa_chirps(lyr):
            capas.append((tiempo_capa(lyr), lyr))
    capas.sort(
        key=lambda x: (
            x[0] is None,
            x[0] or datetime.min.replace(tzinfo=timezone.utc),
        )
    )
    return capas


def capa_frontera(project=None):
    if project is None:
        project = QgsProject.instance()
    for lyr in project.mapLayers().values():
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            continue
        tipo_poly = getattr(QgsWkbTypes, "PolygonGeometry", 2)
        if lyr.geometryType() != tipo_poly:
            continue
        nombre = lyr.name() or ""
        if nombre.startswith("Frontera "):
            return lyr
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


def muestrear_mm_dia(raster, pt_map, crs_map, project):
    """Devuelve mm/día en el punto, o None si no se pudo leer."""
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
    if raster.bandCount() >= 3 and raster.bandCount() != 1:
        # GeoTIFF científico CHIRPS es 1 banda; RGB solo si vino de visualización
        r = res.get(1)
        g = res.get(2)
        b = res.get(3)
        a = res.get(4)
        if g is not None and b is not None:
            try:
                vf = float(r)
            except (TypeError, ValueError):
                vf = None
            # si las 3 bandas coinciden ~valor científico, no es RGB
            if vf is not None and vf > 1.5:
                try:
                    if abs(float(g) - vf) < 0.01 and abs(float(b) - vf) < 0.01:
                        if vf < 0 or vf == NODATA:
                            return 0.0
                        return vf
                except (TypeError, ValueError):
                    pass
            return rgb_a_mmdia(r, g, b, a)
    val = res.get(1)
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if v < 0 or v == NODATA or v < -100:
        return 0.0
    return v


def precipitacion_suma_diaria(muestras):
    """Un valor por día (promedio si hay duplicados). Acumulado = suma."""
    por_dia = defaultdict(list)
    for dt, mm in muestras:
        if dt is None:
            continue
        por_dia[dt.date()].append(float(mm or 0.0))
    diarios = []
    total = 0.0
    for dia in sorted(por_dia):
        vals = por_dia[dia]
        v = sum(vals) / max(len(vals), 1)
        diarios.append((dia, v))
        total += v
    return total, diarios


def asegurar_capa_puntos(project=None):
    if project is None:
        project = QgsProject.instance()
    for lyr in project.mapLayers().values():
        if (lyr.name() or "") == CAPA_PUNTOS and isinstance(lyr, QgsVectorLayer):
            nombres = [f.name() for f in lyr.fields()]
            if "n_dias" not in nombres:
                lyr.dataProvider().addAttributes([QgsField("n_dias", QVariant.Int)])
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
        QgsField("n_dias", QVariant.Int),
        QgsField("max_mm_dia", QVariant.Double),
        QgsField("inicio_utc", QVariant.String),
        QgsField("fin_utc", QVariant.String),
        QgsField("pais", QVariant.String),
    ])
    capa.updateFields()

    symbol = QgsMarkerSymbol.createSimple({
        "name": "circle",
        "color": "180,90,0,220",
        "outline_color": "255,255,255,255",
        "outline_width": "0.6",
        "size": "3.2",
    })
    capa.setRenderer(QgsSingleSymbolRenderer(symbol))

    texto = QgsTextFormat()
    texto.setSize(10)
    texto.setColor(QColor(70, 30, 10))
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


def agregar_punto_resultado(
    capa, lon, lat, total, n, n_lluvia, n_dias, max_mm, t0, t1, pais
):
    feat = QgsFeature(capa.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
    valores = {
        "acumulado_mm": round(total, 2),
        "lon": round(lon, 5),
        "lat": round(lat, 5),
        "n_rasters": int(n),
        "n_lluvia": int(n_lluvia),
        "n_dias": int(n_dias),
        "max_mm_dia": round(max_mm, 2) if max_mm is not None else None,
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
    capas = listar_chirps(project)
    if not capas:
        raise QgsProcessingException(
            "No hay rasters CHIRPS en el proyecto. "
            "Ejecuta primero «CHIRPS país, fechas y video»."
        )

    frontera = capa_frontera(project)
    pais = nombre_pais_frontera(frontera)
    if frontera is None:
        raise QgsProcessingException(
            "No se encontró la capa «Frontera …». "
            "Carga CHIRPS con la opción de frontera del país."
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
            "Sumando precipitación diaria (mm/día)…",
            "Cancelar",
            0,
            len(capas),
            parent,
        )
        progress.setWindowTitle("Acumulado CHIRPS")
        progress.setMinimumDuration(0)
        progress.setValue(0)

    n_ok = 0
    n_lluvia = 0
    max_mm = 0.0
    muestras = []

    try:
        for i, (tiempo, raster) in enumerate(capas):
            if progress is not None:
                progress.setValue(i)
                if progress.wasCanceled():
                    raise QgsProcessingException("Cálculo cancelado.")
            if feedback is not None and feedback.isCanceled():
                raise QgsProcessingException("Cálculo cancelado.")

            mm = muestrear_mm_dia(raster, pt_map, crs_map, project)
            if mm is None:
                continue
            n_ok += 1
            if mm > 0:
                n_lluvia += 1
            if mm > max_mm:
                max_mm = mm
            muestras.append((tiempo, mm))
    finally:
        if progress is not None:
            progress.setValue(len(capas))
            progress.close()

    if n_ok == 0:
        raise QgsProcessingException(
            "No se pudo leer ningún raster CHIRPS en ese punto."
        )

    total, diarios = precipitacion_suma_diaria(muestras)
    tiempos = [t for t, _ in muestras if t is not None]
    t0 = min(tiempos) if tiempos else None
    t1 = max(tiempos) if tiempos else None
    t1_fin = t1 + timedelta(days=1) if t1 is not None else None

    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    pt_geo = transformar_punto(pt_map, crs_map, wgs84, project)

    def fmt(dt):
        if dt is None:
            return "—"
        return dt.strftime("%Y-%m-%d UTC")

    return {
        "total_mm": total,
        "lon": pt_geo.x(),
        "lat": pt_geo.y(),
        "n_rasters": n_ok,
        "n_lluvia": n_lluvia,
        "n_dias": len(diarios),
        "max_mm_dia": max_mm,
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
        "Rasters usados: {n_rasters} de {n_capas}  (1 por día)\n"
        "Días sumados: {n_dias}\n"
        "Días con lluvia: {n_lluvia}\n"
        "Máximo diario: {max_mm:.1f} mm/día\n\n"
        "Escala (mm/día): verde ≈ 5   amarillo ≈ 10–15\n"
        "  naranja ≈ 25–35   rojo ≈ 50–75\n"
        "  rojo oscuro ≈ 100   púrpura ≈ 125–150+\n\n"
        "Fórmula: CHIRPS es mm por día.\n"
        "  acumulado = Σ mm/día del periodo\n"
        "  (no se promedia por hora; no es IMERG)"
    ).format(
        total=r["total_mm"],
        lon=r["lon"],
        lat=r["lat"],
        pais=r["pais"],
        inicio=r["inicio"],
        fin=r["fin"],
        n_rasters=r["n_rasters"],
        n_capas=r["n_capas"],
        n_dias=r["n_dias"],
        n_lluvia=r["n_lluvia"],
        max_mm=r["max_mm_dia"],
    )


def limpiar_cruces(canvas=None):
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
        r["n_dias"],
        r["max_mm_dia"],
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
            "Acumulado CHIRPS",
            "{:.1f} mm".format(r["total_mm"]),
            Qgis.Success,
            10,
        )
    QMessageBox.information(parent, "Acumulado de lluvia CHIRPS", msg)


class MapToolAcumuladoChirps(QgsMapToolEmitPoint):
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
            QMessageBox.warning(parent, "CHIRPS acumulado", str(e))
            return
        except Exception as e:
            QMessageBox.critical(parent, "CHIRPS acumulado", str(e))
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
    capas = listar_chirps()
    if not capas:
        raise QgsProcessingException(
            "No hay rasters CHIRPS cargados. "
            "Ejecuta primero «CHIRPS país, fechas y video»."
        )
    frontera = capa_frontera()
    if frontera is None:
        raise QgsProcessingException(
            "No está la capa de frontera del país. "
            "Vuelve a cargar CHIRPS con «Añadir frontera del país»."
        )

    canvas = iface.mapCanvas()
    limpiar_cruces(canvas)
    tool = MapToolAcumuladoChirps(canvas)
    _TOOL_REF[:] = [tool]
    canvas.setMapTool(tool)
    iface.messageBar().pushMessage(
        "CHIRPS acumulado",
        "Haz clic dentro de {} para sumar los mm/día del periodo. "
        "Esc cancela. {} rasters cargados.".format(
            nombre_pais_frontera(frontera),
            len(capas),
        ),
        Qgis.Info,
        12,
    )
    return len(capas)


class ChirpsAcumuladoClicAlgorithm(QgsProcessingAlgorithm):

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return ChirpsAcumuladoClicAlgorithm()

    def name(self):
        return "chirps_acumulado_clic"

    def displayName(self):
        return self.tr("CHIRPS acumulado al clic")

    def group(self):
        return self.tr("CHIRPS")

    def groupId(self):
        return "chirps_lluvia"

    def shortHelpString(self):
        return self.tr(
            "Activa una herramienta de clic sobre el mapa. "
            "Pulsa un punto DENTRO de la frontera del país: se lee la "
            "misma celda en todos los rasters CHIRPS cargados.\n\n"
            "CHIRPS es mm/día (un raster = un día). El acumulado es:\n"
            "  mm = Σ mm/día del periodo\n\n"
            "No se promedia por hora (eso es IMERG). El valor se anota "
            "en la capa «Acumulado lluvia CHIRPS».\n\n"
            "Escala mm/día: 5, 10, 15, 25, 35, 50, 75, 100, 125, 150."
        )

    def flags(self):
        return _no_threading_flag()

    def initAlgorithm(self, config=None):
        return

    def processAlgorithm(self, parameters, context, feedback):
        n = activar_herramienta()
        feedback.pushInfo(
            "Herramienta activa. Haz clic dentro del país. "
            "Rasters CHIRPS: {}".format(n)
        )
        return {"rasters": n}


def run():
    parent = _parent_window()
    try:
        n = activar_herramienta()
    except Exception as e:
        QMessageBox.critical(parent, "Error CHIRPS acumulado", str(e))
        raise
    QMessageBox.information(
        parent,
        "CHIRPS acumulado al clic",
        "Herramienta lista.\n\n"
        "Haz clic dentro del país para obtener el acumulado "
        "de lluvia del periodo (suma de mm/día).\n\n"
        "Rasters CHIRPS: {}\n"
        "Fórmula: Σ mm/día = mm del periodo\n\n"
        "Esc para salir de la herramienta.".format(n),
    )
    return n


if __name__ == "__main__":
    run()

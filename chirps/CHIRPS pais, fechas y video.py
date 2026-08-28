# ============================================================
# CHIRPS 2.0 — QGIS 3.40 Processing
#
# País + buffer + rango de fechas UTC (un raster por día).
# Los valores son mm/día (no mm/h). Paleta: 5, 10, 15, 25, 35,
# 50, 75, 100, 125, 150 mm/día (y fracciones intermedias).
#
# Fuente: UCSB Climate Hazards Group (GeoTIFF científico).
# Si el día es reciente y el producto final no está, se usa
# CHIRPS prelim. ERDDAP se intenta primero (recorte al país).
#
# Caja de herramientas → Scripts → CHIRPS
#   → CHIRPS país, fechas y video
# ============================================================

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QDate,
    QTime,
    QDateTime,
    Qt,
)
from qgis.PyQt.QtGui import QColor, QPainter, QFont
from qgis.PyQt.QtWidgets import QMessageBox, QProgressDialog

from qgis.core import (
    Qgis,
    QgsProcessingAlgorithm,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFile,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterDateTime,
    QgsProcessingException,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsSingleSymbolRenderer,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsRasterTransparency,
    QgsDateTimeRange,
    QgsInterval,
    QgsTemporalNavigationObject,
    QgsRectangle,
)

import os
import io
import re
import glob
import json
import ssl
import gzip
import shutil
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone


DIAS_DEF = 15
OPACIDAD = 180
MAX_FRAMES = 180
WEST = 79.0
EAST = 92.0
SOUTH = 24.0
NORTH = 33.0
BUFFER_METROS = 200000
PAIS_DEF = "Nepal"
MIN_BYTES_VALIDO = 800
NODATA = -9999.0

UCSB_FINAL = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/"
    "global_daily/tifs/p05/{year}/chirps-v2.0.{stamp}.tif.gz"
)
UCSB_PRELIM = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/"
    "prelim/global_daily/tifs/p05/{year}/chirps-v2.0.{stamp}.tif.gz"
)
ERDDAP_TIF = (
    "https://coastwatch.pfeg.noaa.gov/erddap/griddap/"
    "chirps20GlobalDailyP05.tif"
)

COUNTRIES_URL = (
    "https://raw.githubusercontent.com/"
    "datasets/geo-countries/master/data/countries.geojson"
)

USER_AGENT = "QGIS-3.40-CHIRPS"
SSL_CONTEXT = ssl.create_default_context()

PAISES_FALLBACK = [
    "Nepal", "India", "China", "Bhutan", "Bangladesh", "Pakistan",
    "Costa Rica", "Nicaragua", "Honduras", "El Salvador", "Guatemala",
    "Belize", "Panama", "Mexico", "Colombia", "Ecuador", "Peru",
    "Bolivia", "Chile", "Argentina", "Brazil", "Paraguay", "Uruguay",
    "United States of America", "Canada", "Spain", "Portugal",
    "Kenya", "Ethiopia", "Tanzania", "Indonesia", "Philippines",
    "Vietnam", "Thailand", "Myanmar", "Japan", "Australia",
]

# mm/día → RGB. Verde (poca lluvia) a púrpura (≥150 mm/día).
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

PATRON_FRAME = re.compile(r"CHIRPS_(\d{8})\.tif$", re.IGNORECASE)


def _folder_behavior():
    behavior = getattr(QgsProcessingParameterFile, "Behavior", None)
    if behavior is not None and hasattr(behavior, "Folder"):
        return behavior.Folder
    return QgsProcessingParameterFile.Folder


def _number_integer():
    tipo = getattr(QgsProcessingParameterNumber, "Type", None)
    if tipo is not None and hasattr(tipo, "Integer"):
        return tipo.Integer
    return QgsProcessingParameterNumber.Integer


def _datetime_type():
    tipo = getattr(QgsProcessingParameterDateTime, "DateTime", None)
    if tipo is not None:
        return tipo
    inner = getattr(QgsProcessingParameterDateTime, "Type", None)
    if inner is not None and hasattr(inner, "DateTime"):
        return inner.DateTime
    return 2


def slug_pais(pais):
    texto = "".join(c if c.isalnum() else "_" for c in (pais or "pais"))
    return texto.strip("_") or "pais"


def carpeta_padre_defecto():
    return os.path.join(os.path.expanduser("~"), "CHIRPS_UCSB")


def nombre_proyecto(pais, inicio, fin):
    slug = slug_pais(pais)
    a = inicio.strftime("%Y%m%d")
    b = fin.strftime("%Y%m%d")
    if a == b:
        return "CHIRPS_{}_{}".format(slug, a)
    return "CHIRPS_{}_{}_{}".format(slug, a, b)


def preparar_proyecto(padre, pais, inicio, fin, log=None):
    if padre is None or str(padre).strip() == "":
        padre = carpeta_padre_defecto()
    padre = os.path.abspath(str(padre).strip())
    os.makedirs(padre, exist_ok=True)
    raiz = os.path.join(padre, nombre_proyecto(pais, inicio, fin))
    frames_dir = os.path.join(raiz, "frames")
    png_dir = os.path.join(raiz, "png")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(png_dir, exist_ok=True)
    if log is not None:
        log("Proyecto: " + raiz)
        log("  frames -> " + frames_dir)
        log("  png    -> " + png_dir)
    return raiz, frames_dir, png_dir


def aplicar_bbox(west, south, east, north):
    global WEST, SOUTH, EAST, NORTH
    WEST = float(west)
    SOUTH = float(south)
    EAST = float(east)
    NORTH = float(north)


def alinear_dia(dt):
    dt = dt.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return dt


def qdatetime_a_utc(qdt):
    if qdt is None or not qdt.isValid():
        return None
    if hasattr(qdt, "toUTC"):
        qdt = qdt.toUTC()
    py = qdt.toPyDateTime()
    if py.tzinfo is None:
        py = py.replace(tzinfo=timezone.utc)
    else:
        py = py.astimezone(timezone.utc)
    return alinear_dia(py)


def fechas_defecto_qdt():
    fin = alinear_dia(datetime.now(timezone.utc) - timedelta(days=2))
    inicio = fin - timedelta(days=DIAS_DEF - 1)

    def make(dt):
        qdt = QDateTime(
            QDate(dt.year, dt.month, dt.day),
            QTime(0, 0),
        )
        spec = getattr(Qt, "UTC", getattr(getattr(Qt, "TimeSpec", Qt), "UTC"))
        qdt.setTimeSpec(spec)
        return qdt

    return make(inicio), make(fin)


def _nombres_desde_geojson(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    nombres = []
    vistos = set()
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        raw = None
        for k in ("name", "ADMIN", "NAME", "NAME_EN", "NAME_LONG"):
            if props.get(k):
                raw = str(props[k]).strip()
                break
        if not raw:
            continue
        key = raw.lower()
        if key in vistos:
            continue
        vistos.add(key)
        nombres.append(raw)
    nombres.sort(key=lambda s: s.lower())
    return nombres


def lista_paises():
    nombres = []
    candidatos = [
        os.path.join(os.path.expanduser("~"), "CHIRPS_UCSB", "countries.geojson"),
        os.path.join(os.path.expanduser("~"), "IMERG_NASA", "countries.geojson"),
        os.path.join(os.path.expanduser("~"), "IMERG_NASA_Nepal_Tibet", "countries.geojson"),
    ]
    for path in candidatos:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                nombres = _nombres_desde_geojson(path)
                if nombres:
                    break
        except Exception:
            continue
    if not nombres:
        nombres = list(PAISES_FALLBACK)
    resto = [n for n in nombres if n.lower() != "nepal"]
    return ["Nepal"] + resto


def indice_pais(nombres, pais=PAIS_DEF):
    clave = (pais or PAIS_DEF).lower()
    for i, n in enumerate(nombres):
        if n.lower() == clave:
            return i
    return 0


def buscar_ffmpeg():
    candidatos = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "ffmpeg",
            "bin",
            "ffmpeg.exe",
        ),
    ]
    which = shutil.which("ffmpeg")
    if which:
        candidatos.insert(0, which)
    for c in candidatos:
        if c and os.path.isfile(c):
            return c
    return None


def generar_mp4(pngs, fps, video_path, log):
    if not pngs:
        return None
    ffmpeg = buscar_ffmpeg()
    if not ffmpeg:
        log("No se encontró ffmpeg; el MP4 no se generó.")
        log("Coloca ffmpeg en C:\\ffmpeg\\bin\\ffmpeg.exe")
        return None
    patron = os.path.join(os.path.dirname(pngs[0]), "chirps_%04d.png")
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    cmd = [
        ffmpeg, "-y",
        "-framerate", str(max(int(fps), 1)),
        "-i", patron,
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
        "-c:v", "libx264", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        video_path,
    ]
    log("Generando video MP4 con ffmpeg...")
    proc = subprocess.run(cmd, **kwargs)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[-800:]
        raise RuntimeError("ffmpeg falló:\n" + err)
    log("Video: " + video_path)
    return video_path


def _listar_tiffs(carpeta, t0=None, t1=None):
    archivos = []
    for ruta in glob.glob(os.path.join(carpeta, "CHIRPS_*.tif")):
        m = PATRON_FRAME.match(os.path.basename(ruta))
        if not m:
            continue
        tiempo = datetime.strptime(m.group(1), "%Y%m%d").replace(
            tzinfo=timezone.utc
        )
        if t0 is not None and t1 is not None:
            a, b = t0, t1
            if getattr(a, "tzinfo", None) is None:
                a = a.replace(tzinfo=timezone.utc)
            if getattr(b, "tzinfo", None) is None:
                b = b.replace(tzinfo=timezone.utc)
            if not (min(a, b).date() <= tiempo.date() <= max(a, b).date()):
                continue
        archivos.append((tiempo, ruta))
    archivos.sort(key=lambda x: x[0])
    return archivos


def _descargar_osm_fondo(ancho, alto, log):
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": "OSM-WMS",
        "STYLES": "",
        "SRS": "EPSG:4326",
        "BBOX": "{},{},{},{}".format(WEST, SOUTH, EAST, NORTH),
        "WIDTH": str(int(ancho)),
        "HEIGHT": str(int(alto)),
        "FORMAT": "image/png",
    }
    url = "https://ows.terrestris.de/osm/service?" + urllib.parse.urlencode(params)
    log("Descargando mapa base OSM...")
    data = descargar_bytes(url, timeout=120)
    from PIL import Image
    import numpy as np
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    return np.array(img)


def _lluvia_rgba(tif_path, ancho, alto):
    from osgeo import gdal
    import numpy as np
    gdal.UseExceptions()
    src = gdal.Open(tif_path)
    if src is None:
        return None
    ds = gdal.Translate("", src, format="MEM", width=int(ancho), height=int(alto))
    src = None
    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    nd = ds.GetRasterBand(1).GetNoDataValue()
    ds = None
    if nd is None:
        nd = NODATA
    mask = np.isfinite(arr) & (arr > 0.05) & (arr != nd) & (arr > -100)
    r = np.zeros(arr.shape, dtype=np.uint8)
    g = np.zeros(arr.shape, dtype=np.uint8)
    b = np.zeros(arr.shape, dtype=np.uint8)
    if not np.any(mask):
        return r, g, b, mask
    umbrales = np.array([p[0] for p in RAMP_MM_DIA], dtype=np.float32)
    r_ch = np.array([p[1] for p in RAMP_MM_DIA], dtype=np.float32)
    g_ch = np.array([p[2] for p in RAMP_MM_DIA], dtype=np.float32)
    b_ch = np.array([p[3] for p in RAMP_MM_DIA], dtype=np.float32)
    vals = arr[mask]
    r[mask] = np.clip(np.interp(vals, umbrales, r_ch), 0, 255).astype(np.uint8)
    g[mask] = np.clip(np.interp(vals, umbrales, g_ch), 0, 255).astype(np.uint8)
    b[mask] = np.clip(np.interp(vals, umbrales, b_ch), 0, 255).astype(np.uint8)
    return r, g, b, mask


def _dibujar_reloj_pil(img, texto):
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    if hasattr(draw, "textbbox"):
        caja = draw.textbbox((0, 0), texto, font=font)
        tw, th = caja[2] - caja[0], caja[3] - caja[1]
    else:
        tw, th = draw.textsize(texto, font=font)
    pad = 8
    draw.rectangle(
        [12, 12, 12 + tw + pad * 2, 12 + th + pad * 2],
        fill=(0, 0, 0, 160),
    )
    draw.text((12 + pad, 12 + pad), texto, fill=(255, 255, 255, 255), font=font)
    return img


def exportar_png_y_video(
    frames_dir,
    png_dir,
    video_dir,
    pais,
    fps,
    ancho,
    fecha_inicio,
    fecha_fin,
    export_png=True,
    export_gif=False,
    export_video=True,
    feedback=None,
):
    from PIL import Image

    def log(msg):
        print(msg)
        if feedback is not None:
            feedback.pushInfo(str(msg))

    os.makedirs(png_dir, exist_ok=True)
    os.makedirs(video_dir, exist_ok=True)
    frames = _listar_tiffs(frames_dir, fecha_inicio, fecha_fin)
    if not frames:
        frames = _listar_tiffs(frames_dir)
    if not frames:
        raise QgsProcessingException(
            "No hay GeoTIFF CHIRPS para exportar en:\n" + frames_dir
        )

    ratio = max((EAST - WEST) / max(NORTH - SOUTH, 0.01), 0.2)
    alto = max(int(ancho / ratio), 1)
    fondo = _descargar_osm_fondo(ancho, alto, log)
    log("Exportando PNG a: " + png_dir)

    pngs = []
    total = len(frames)
    for i, (tiempo, ruta) in enumerate(frames):
        if feedback is not None and feedback.isCanceled():
            break
        if feedback is not None:
            feedback.setProgress(55 + int(35 * i / max(total, 1)))
            feedback.setProgressText("PNG " + tiempo.strftime("%Y-%m-%d"))
        lluvia = _lluvia_rgba(ruta, fondo.shape[1], fondo.shape[0])
        out = fondo.copy()
        if lluvia is not None:
            r, g, b, mask = lluvia
            hh = min(r.shape[0], out.shape[0])
            ww = min(r.shape[1], out.shape[1])
            m = mask[:hh, :ww]
            out[:hh, :ww][m, 0] = r[:hh, :ww][m]
            out[:hh, :ww][m, 1] = g[:hh, :ww][m]
            out[:hh, :ww][m, 2] = b[:hh, :ww][m]
            out[:hh, :ww][m, 3] = 255
        img = Image.fromarray(out)
        _dibujar_reloj_pil(img, tiempo.strftime("%Y-%m-%d UTC  (CHIRPS mm/día)"))
        png_path = os.path.join(png_dir, "chirps_{:04d}.png".format(i + 1))
        if export_png or export_gif or export_video:
            img.save(png_path, "PNG")
            pngs.append(png_path)

    log("PNG exportados: " + str(len(pngs)))
    gif_path = ""
    if export_gif and pngs:
        try:
            gif_path = os.path.join(video_dir, "CHIRPS_{}.gif".format(slug_pais(pais)))
            seq = [Image.open(p).convert("RGB") for p in pngs]
            seq[0].save(
                gif_path,
                save_all=True,
                append_images=seq[1:],
                duration=max(int(1000 / max(fps, 0.5)), 50),
                loop=0,
            )
            log("GIF: " + gif_path)
        except Exception as e:
            log("GIF no generado: " + str(e))
            gif_path = ""

    video = ""
    if export_video and pngs:
        video_path = os.path.join(video_dir, "CHIRPS_{}.mp4".format(slug_pais(pais)))
        video = generar_mp4(pngs, fps, video_path, log) or ""

    if feedback is not None:
        feedback.setProgress(100)
    return {
        "pngs": len(pngs),
        "directorio": png_dir,
        "gif": gif_path,
        "video": video,
        "proyecto": video_dir,
    }


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


def poner_osm_al_fondo(root=None):
    project = QgsProject.instance()
    if root is None:
        root = project.layerTreeRoot()
    for lyr in list(project.mapLayers().values()):
        nombre = (lyr.name() or "").lower()
        if "openstreetmap" not in nombre:
            continue
        node = root.findLayer(lyr.id())
        if node is None:
            continue
        parent = node.parent()
        if parent is None:
            continue
        cloned = node.clone()
        parent.removeChildNode(node)
        parent.addChildNode(cloned)


def zoom_al_pais(iface, project):
    rect = QgsRectangle(WEST, SOUTH, EAST, NORTH)
    origen = QgsCoordinateReferenceSystem("EPSG:4326")
    destino = iface.mapCanvas().mapSettings().destinationCrs()
    if destino.isValid() and destino != origen:
        xform = QgsCoordinateTransform(origen, destino, project)
        rect = xform.transformBoundingBox(rect)
    iface.mapCanvas().setExtent(rect)
    iface.mapCanvas().refresh()


def descargar_bytes(url, timeout=180):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=SSL_CONTEXT,
        ) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP {} — {}".format(e.code, url))


def es_tiff(data):
    return data[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def nombre_pais(feature):
    for campo in ("name", "ADMIN", "NAME", "NAME_EN", "NAME_LONG"):
        if campo in feature.fields().names():
            valor = feature[campo]
            if valor:
                return str(valor).strip().lower()
    return ""


def recortar_a_bbox(src_path, dst_path):
    from osgeo import gdal
    gdal.UseExceptions()
    gdal.Warp(
        dst_path,
        src_path,
        format="GTiff",
        outputBounds=[WEST, SOUTH, EAST, NORTH],
        dstSRS="EPSG:4326",
        dstNodata=NODATA,
        resampleAlg="near",
        creationOptions=["COMPRESS=LZW", "TILED=YES"],
    )


def url_erddap(tiempo):
    t = tiempo.strftime("%Y-%m-%dT00:00:00Z")
    query = "precip[({t})][({s}):({n})][({w}):({e})]".format(
        t=t, s=SOUTH, n=NORTH, w=WEST, e=EAST
    )
    return ERDDAP_TIF + "?" + urllib.parse.quote(query, safe="[]():,.")


def urls_ucsb(tiempo):
    stamp = tiempo.strftime("%Y.%m.%d")
    year = tiempo.strftime("%Y")
    prelim = UCSB_PRELIM.format(year=year, stamp=stamp)
    final = UCSB_FINAL.format(year=year, stamp=stamp)
    lag = (datetime.now(timezone.utc).date() - tiempo.date()).days
    if lag <= 21:
        return [prelim, final]
    return [final, prelim]


def _guardar_global_y_recortar(data, salida):
    tmp = salida + ".global.tif"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        recortar_a_bbox(tmp, salida)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def solicitar_frame(tiempo, salida):
    errores = []
    try:
        url = url_erddap(tiempo)
        data = descargar_bytes(url, timeout=45)
        if es_tiff(data) and len(data) >= MIN_BYTES_VALIDO:
            with open(salida, "wb") as f:
                f.write(data)
            return url
        errores.append("ERDDAP no devolvió GeoTIFF")
    except Exception as e:
        errores.append("ERDDAP: " + str(e))

    for url in urls_ucsb(tiempo):
        try:
            data = descargar_bytes(url, timeout=180)
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            if not es_tiff(data):
                errores.append("No es TIFF: " + url)
                continue
            _guardar_global_y_recortar(data, salida)
            if os.path.exists(salida) and os.path.getsize(salida) >= MIN_BYTES_VALIDO:
                return url
            errores.append("Recorte vacío: " + url)
        except Exception as e:
            errores.append(str(e))
            continue

    raise RuntimeError(
        "No se pudo descargar CHIRPS para "
        + tiempo.strftime("%Y-%m-%d")
        + ".\n"
        + "\n".join(errores[-4:])
    )


def necesita_descarga(path):
    if not os.path.exists(path):
        return True
    return os.path.getsize(path) < MIN_BYTES_VALIDO


def aplicar_simbologia(raster):
    shader = QgsRasterShader()
    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Interpolated)

    def item(valor, r, g, b, etiqueta):
        return QgsColorRampShader.ColorRampItem(
            valor,
            QColor(r, g, b, OPACIDAD),
            etiqueta,
        )

    ramp.setColorRampItemList([
        item(mm, r, g, b, "{:g} mm/día".format(mm))
        for mm, r, g, b in RAMP_MM_DIA
    ])
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(
        raster.dataProvider(),
        1,
        shader,
    )
    try:
        renderer.setClassificationMin(0.0)
        renderer.setClassificationMax(150.0)
    except Exception:
        pass

    transparency = QgsRasterTransparency()
    try:
        transparency.setTransparentSingleValuePixelList([
            QgsRasterTransparency.TransparentSingleValuePixel(0.0, 0.05, 100.0),
            QgsRasterTransparency.TransparentSingleValuePixel(NODATA, NODATA, 100.0),
        ])
    except TypeError:
        p0 = QgsRasterTransparency.TransparentSingleValuePixel()
        p0.min = 0.0
        p0.max = 0.05
        p0.percentTransparent = 100
        transparency.setTransparentSingleValuePixelList([p0])
    renderer.setRasterTransparency(transparency)

    raster.setRenderer(renderer)
    try:
        modo = getattr(QPainter, "CompositionMode_SourceOver", None)
        if modo is None:
            modo = QPainter.CompositionMode.SourceOver
        raster.setBlendMode(modo)
    except Exception:
        pass
    raster.triggerRepaint()


def _qt_utc():
    return getattr(Qt, "UTC", getattr(getattr(Qt, "TimeSpec", Qt), "UTC"))


def qdatetime_utc(dt):
    qdt = QDateTime(
        QDate(dt.year, dt.month, dt.day),
        QTime(dt.hour, dt.minute, dt.second),
    )
    qdt.setTimeSpec(_qt_utc())
    return qdt


def _modo_rango_fijo():
    modo = getattr(Qgis, "RasterTemporalMode", None)
    if modo is not None and hasattr(modo, "FixedTemporalRange"):
        return modo.FixedTemporalRange
    from qgis.core import QgsRasterLayerTemporalProperties
    return QgsRasterLayerTemporalProperties.ModeFixedTemporalRange


def activar_temporal(raster, tiempo):
    inicio = qdatetime_utc(tiempo)
    fin = qdatetime_utc(tiempo + timedelta(days=1))
    props = raster.temporalProperties()
    props.setMode(_modo_rango_fijo())
    props.setFixedTemporalRange(QgsDateTimeRange(inicio, fin, True, False))
    props.setIsActive(True)


def _intervalo_dias(n):
    unidad = getattr(Qgis, "TemporalUnit", None)
    if unidad is not None and hasattr(unidad, "Days"):
        return QgsInterval(n, unidad.Days)
    return QgsInterval(float(n) * 86400.0)


def configurar_controlador_temporal(capas, fps=4.0):
    iface = _iface()
    if iface is None or not capas:
        return
    t0 = capas[0][0]
    t1 = capas[-1][0] + timedelta(days=1)
    nav = iface.mapCanvas().temporalController()
    nav.setTemporalExtents(
        QgsDateTimeRange(qdatetime_utc(t0), qdatetime_utc(t1), True, False)
    )
    nav.setFrameDuration(_intervalo_dias(1))
    nav.setFramesPerSecond(float(fps))
    modo = QgsTemporalNavigationObject.Animated
    nav_mode = getattr(QgsTemporalNavigationObject, "NavigationMode", None)
    if nav_mode is not None and hasattr(nav_mode, "Animated"):
        modo = nav_mode.Animated
    nav.setNavigationMode(modo)
    nav.rewindToStart()


def asegurar_osm(project=None, root=None, log=None):
    if project is None:
        project = QgsProject.instance()
    if root is None:
        root = project.layerTreeRoot()

    def _log(msg):
        if log is not None:
            log(msg)

    for lyr in project.mapLayers().values():
        if "openstreetmap" in (lyr.name() or "").lower() and lyr.isValid():
            return lyr

    osm_uri = (
        "type=xyz&url=https://tile.openstreetmap.org/"
        "%7Bz%7D/%7Bx%7D/%7By%7D.png&zmin=0&zmax=19"
    )
    osm = QgsRasterLayer(osm_uri, "OpenStreetMap Standard", "wms")
    if osm.isValid():
        project.addMapLayer(osm, False)
        root.addLayer(osm)
        _log("OSM Standard: OK (basemap al fondo)")
        return osm
    _log("OSM no pudo cargarse.")
    return None


def asegurar_frontera(
    project=None,
    root=None,
    output_dir=None,
    log=None,
    pais=PAIS_DEF,
    buffer_metros=None,
):
    if project is None:
        project = QgsProject.instance()
    if root is None:
        root = project.layerTreeRoot()

    def _log(msg):
        if log is not None:
            log(msg)

    pais = (pais or PAIS_DEF).strip()
    buf = int(buffer_metros if buffer_metros is not None else BUFFER_METROS)
    km = max(int(round(buf / 1000.0)), 0)
    nombre_frontera = "Frontera " + pais
    nombre_buffer = "{} + {} km".format(pais, km)

    ya_frontera = False
    ya_buffer = False
    for lyr in project.mapLayers().values():
        n = lyr.name() or ""
        if n == nombre_frontera:
            ya_frontera = True
        elif n == nombre_buffer:
            ya_buffer = True
    if ya_frontera and ya_buffer:
        for lyr in project.mapLayers().values():
            if (lyr.name() or "") == nombre_buffer:
                rect = lyr.extent()
                aplicar_bbox(
                    max(-180.0, rect.xMinimum()),
                    max(-50.0, rect.yMinimum()),
                    min(180.0, rect.xMaximum()),
                    min(50.0, rect.yMaximum()),
                )
                break
        return True

    if output_dir is None or str(output_dir).strip() == "":
        output_dir = carpeta_padre_defecto()
    os.makedirs(output_dir, exist_ok=True)

    feat_pais = None
    paises = None
    geojson_path = os.path.join(output_dir, "countries.geojson")
    fallbacks = [
        os.path.join(os.path.expanduser("~"), "CHIRPS_UCSB", "countries.geojson"),
        os.path.join(os.path.expanduser("~"), "IMERG_NASA_Nepal_Tibet", "countries.geojson"),
    ]
    try:
        if not os.path.exists(geojson_path) or os.path.getsize(geojson_path) < 1000:
            usado = None
            for fb in fallbacks:
                if os.path.exists(fb) and os.path.getsize(fb) > 1000:
                    usado = fb
                    break
            if usado:
                geojson_path = usado
            else:
                with open(geojson_path, "wb") as f:
                    f.write(descargar_bytes(COUNTRIES_URL, timeout=120))
        paises = QgsVectorLayer(geojson_path, "Paises", "ogr")
        clave = pais.lower()
        if paises.isValid():
            for feature in paises.getFeatures():
                if nombre_pais(feature) == clave:
                    feat_pais = feature
                    break
        else:
            _log("No se pudo leer countries.geojson.")
    except Exception as e:
        _log("No se pudo descargar el polígono de países: " + str(e))

    if not feat_pais:
        _log("No se encontró el polígono de " + pais + "; se usa el bbox regional.")
        return False

    transform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsCoordinateReferenceSystem("EPSG:3857"),
        project,
    )
    geom_3857 = QgsGeometry(feat_pais.geometry())
    geom_3857.transform(transform)
    buffer_geom = geom_3857.buffer(buf, 20)
    transform_back = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:3857"),
        QgsCoordinateReferenceSystem("EPSG:4326"),
        project,
    )
    buffer_geom.transform(transform_back)
    rect = buffer_geom.boundingBox()
    aplicar_bbox(
        max(-180.0, rect.xMinimum()),
        max(-50.0, rect.yMinimum()),
        min(180.0, rect.xMaximum()),
        min(50.0, rect.yMaximum()),
    )

    if not ya_frontera:
        frontera_layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326",
            nombre_frontera,
            "memory",
        )
        prov = frontera_layer.dataProvider()
        if paises is not None:
            prov.addAttributes(paises.fields())
            frontera_layer.updateFields()
        prov.addFeature(feat_pais)
        frontera_layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsFillSymbol.createSimple({
                    "color": "0,0,0,0",
                    "outline_color": "00FFFF",
                    "outline_width": "1.5",
                })
            )
        )
        project.addMapLayer(frontera_layer, False)
        root.insertLayer(0, frontera_layer)
        _log(nombre_frontera + ": OK")

    if not ya_buffer:
        buffer_layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326",
            nombre_buffer,
            "memory",
        )
        feat = QgsFeature()
        feat.setGeometry(buffer_geom)
        buffer_layer.dataProvider().addFeature(feat)
        buffer_layer.updateExtents()
        buffer_layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsFillSymbol.createSimple({
                    "color": "255,0,255,20",
                    "outline_color": "FF00FF",
                    "outline_width": "1",
                })
            )
        )
        project.addMapLayer(buffer_layer, False)
        root.insertLayer(1, buffer_layer)
        _log("Buffer {} km: OK".format(km))
    return True


def ejecutar_chirps(
    dias=DIAS_DEF,
    output_dir=None,
    add_osm=True,
    add_frontera=True,
    feedback=None,
    pais=PAIS_DEF,
    fecha_inicio=None,
    fecha_fin=None,
    buffer_km=200,
    cargar_capas=True,
):
    pais = (pais or PAIS_DEF).strip()
    buf_m = int(float(buffer_km) * 1000)

    if fecha_inicio is not None and fecha_fin is not None:
        inicio = alinear_dia(fecha_inicio)
        fin = alinear_dia(fecha_fin)
    else:
        fin = alinear_dia(datetime.now(timezone.utc) - timedelta(days=2))
        inicio = fin - timedelta(days=int(dias) - 1)
    if fin < inicio:
        inicio, fin = fin, inicio

    if output_dir is None or str(output_dir).strip() == "":
        output_dir = os.path.join(
            carpeta_padre_defecto(),
            nombre_proyecto(pais, inicio, fin),
        )
    os.makedirs(output_dir, exist_ok=True)
    frames_dir = os.path.join(output_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "png"), exist_ok=True)

    def log(msg):
        print(msg)
        if feedback is not None:
            feedback.pushInfo(str(msg))

    def cancelado():
        return feedback is not None and feedback.isCanceled()

    tiempos = []
    t = inicio
    while t <= fin:
        tiempos.append(t)
        t += timedelta(days=1)

    if len(tiempos) > MAX_FRAMES:
        raise QgsProcessingException(
            "Demasiados días ({}). El máximo es {}.".format(
                len(tiempos), MAX_FRAMES
            )
        )

    project = QgsProject.instance()
    root = project.layerTreeRoot()

    log("=" * 75)
    log("CHIRPS 2.0 — " + pais + "  (mm/día)")
    log("=" * 75)
    log("Directorio: " + output_dir)
    log("Inicio UTC: " + inicio.strftime("%Y-%m-%d"))
    log("Fin UTC:    " + fin.strftime("%Y-%m-%d"))
    log("Días:       " + str(len(tiempos)))

    grupo_nombre = "CHIRPS - " + pais
    grupo_anterior = root.findGroup(grupo_nombre)
    if grupo_anterior:
        root.removeChildNode(grupo_anterior)
    grupo = root.addGroup(grupo_nombre)

    if add_osm:
        asegurar_osm(project, root, log)
    if add_frontera:
        asegurar_frontera(
            project,
            root,
            output_dir,
            log,
            pais=pais,
            buffer_metros=buf_m,
        )

    bbox = "{},{},{},{}".format(WEST, SOUTH, EAST, NORTH)
    progress = None
    if feedback is None:
        progress = QProgressDialog(
            "Consultando CHIRPS...",
            "Cancelar",
            0,
            len(tiempos),
            _parent_window(),
        )
        progress.setWindowTitle("CHIRPS - " + pais)
        progress.setMinimumDuration(0)

    capas = []
    errores = 0

    for i, tiempo in enumerate(tiempos):
        if cancelado():
            log("Proceso cancelado.")
            break
        if progress is not None:
            progress.setValue(i)
            progress.setLabelText("CHIRPS " + tiempo.strftime("%Y-%m-%d UTC"))
            if progress.wasCanceled():
                log("Proceso cancelado.")
                break
        if feedback is not None and len(tiempos) > 0:
            feedback.setProgress(int(50 * i / len(tiempos)))
            feedback.setProgressText("Descarga " + tiempo.strftime("%Y-%m-%d"))

        nombre_archivo = "CHIRPS_" + tiempo.strftime("%Y%m%d") + ".tif"
        salida = os.path.join(frames_dir, nombre_archivo)

        if necesita_descarga(salida):
            try:
                url = solicitar_frame(tiempo, salida)
                log(tiempo.strftime("%Y-%m-%d UTC") + "  OK")
                log(url)
            except Exception as e:
                errores += 1
                log("ERROR " + tiempo.strftime("%Y-%m-%d UTC") + ": " + str(e))
                continue

        if not cargar_capas:
            if os.path.exists(salida):
                capas.append((tiempo, None))
            continue

        nombre_capa = "CHIRPS " + tiempo.strftime("%d-%m-%Y")
        raster = QgsRasterLayer(salida, nombre_capa)
        if not raster.isValid():
            errores += 1
            log("Raster inválido: " + salida)
            continue

        aplicar_simbologia(raster)
        activar_temporal(raster, tiempo)
        raster.setCustomProperty("CHIRPS/start", tiempo.isoformat())
        raster.setCustomProperty(
            "CHIRPS/end",
            (tiempo + timedelta(days=1)).isoformat(),
        )
        raster.setCustomProperty(
            "CHIRPS/UTC",
            tiempo.strftime("%Y-%m-%d UTC"),
        )

        project.addMapLayer(raster, False)
        grupo.addLayer(raster)
        capas.append((tiempo, raster))

    try:
        grupo.setExpanded(True)
        grupo.setItemVisibilityChecked(True)
    except Exception:
        pass

    poner_osm_al_fondo(root)

    if progress is not None:
        progress.setValue(len(tiempos))

    log("=" * 75)
    log("Días generados: " + str(len(capas)))
    if errores:
        log("Días con error: " + str(errores))
    log("Periodo: " + inicio.strftime("%Y-%m-%d")
        + " → " + fin.strftime("%Y-%m-%d"))
    log("BBOX: " + bbox)
    log("Carpeta: " + frames_dir)
    log("=" * 75)

    iface = _iface()
    if iface is not None and capas and cargar_capas:
        zoom_al_pais(iface, project)
        configurar_controlador_temporal(capas)

    return {
        "frames": len(capas),
        "errores": errores,
        "directorio": frames_dir,
        "output_dir": output_dir,
        "pais": pais,
        "inicio": inicio,
        "fin": fin,
    }


def ejecutar_completo(
    pais=PAIS_DEF,
    fecha_inicio=None,
    fecha_fin=None,
    buffer_km=200,
    output_dir=None,
    add_osm=True,
    add_frontera=True,
    fps=4,
    ancho=1280,
    export_png=True,
    export_gif=True,
    export_video=True,
    feedback=None,
):
    pais = (pais or PAIS_DEF).strip()
    if fecha_inicio is not None and fecha_fin is not None:
        inicio = alinear_dia(fecha_inicio)
        fin = alinear_dia(fecha_fin)
    else:
        fin = alinear_dia(datetime.now(timezone.utc) - timedelta(days=2))
        inicio = fin - timedelta(days=DIAS_DEF - 1)
    if fin < inicio:
        inicio, fin = fin, inicio

    def log(msg):
        print(msg)
        if feedback is not None:
            feedback.pushInfo(str(msg))

    raiz, frames_dir, png_dir = preparar_proyecto(
        output_dir, pais, inicio, fin, log
    )

    desc = ejecutar_chirps(
        output_dir=raiz,
        add_osm=add_osm,
        add_frontera=add_frontera,
        feedback=feedback,
        pais=pais,
        fecha_inicio=inicio,
        fecha_fin=fin,
        buffer_km=buffer_km,
        cargar_capas=True,
    )
    if desc["frames"] == 0:
        raise QgsProcessingException(
            "No se generó ningún raster CHIRPS. "
            "Revisa el registro y la conexión a UCSB CHIRPS."
        )

    resultado = exportar_png_y_video(
        frames_dir=desc["directorio"],
        png_dir=png_dir,
        video_dir=raiz,
        pais=pais,
        fps=fps,
        ancho=ancho,
        fecha_inicio=desc["inicio"],
        fecha_fin=desc["fin"],
        export_png=export_png,
        export_gif=export_gif,
        export_video=export_video,
        feedback=feedback,
    )
    resultado["frames"] = desc["frames"]
    resultado["frames_descarga"] = desc["frames"]
    resultado["directorio_frames"] = desc["directorio"]
    resultado["directorio"] = raiz
    resultado["pais"] = pais
    if feedback is not None:
        feedback.pushInfo("Capas cargadas en QGIS: " + str(desc["frames"]))
        feedback.pushInfo("Proyecto: " + raiz)
        if resultado.get("video"):
            feedback.pushInfo("Video: " + resultado["video"])
        if resultado.get("gif"):
            feedback.pushInfo("GIF: " + resultado["gif"])
    return resultado


class ChirpsPaisFechasVideoAlgorithm(QgsProcessingAlgorithm):

    PAIS = "PAIS"
    FECHA_INICIO = "FECHA_INICIO"
    FECHA_FIN = "FECHA_FIN"
    BUFFER_KM = "BUFFER_KM"
    OUTPUT_DIR = "OUTPUT_DIR"
    ADD_OSM = "ADD_OSM"
    ADD_FRONTERA = "ADD_FRONTERA"
    FPS = "FPS"
    ANCHO = "ANCHO"
    EXPORT_PNG = "EXPORT_PNG"
    EXPORT_GIF = "EXPORT_GIF"
    EXPORT_VIDEO = "EXPORT_VIDEO"

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return ChirpsPaisFechasVideoAlgorithm()

    def name(self):
        return "chirps_pais_fechas_video"

    def displayName(self):
        return self.tr("CHIRPS país, fechas y video")

    def group(self):
        return self.tr("CHIRPS")

    def groupId(self):
        return "chirps_lluvia"

    def shortHelpString(self):
        return self.tr(
            "Descarga CHIRPS 2.0 diario (mm/día) para el país y el rango "
            "de fechas UTC, carga los GeoTIFF en QGIS, exporta PNG y "
            "genera GIF/MP4 con ffmpeg.\n\n"
            "CHIRPS no es horario: un raster = un día. El acumulado del "
            "periodo es la suma de los mm/día (script «CHIRPS acumulado "
            "al clic»).\n\n"
            "Paleta (mm/día): 5, 10, 15, 25, 35, 50, 75, 100, 125, 150 "
            "(con fracciones 2.5, 7.5, 12.5, 20, 30, 40, 60, 90, 200).\n\n"
            "En la carpeta que elijas se crea:\n"
            "  CHIRPS_Pais_inicio_fin/\n"
            "    frames/   GeoTIFF (valores científicos mm/día)\n"
            "    png/      secuencia PNG\n"
            "    CHIRPS_Pais.mp4  y/o  .gif\n\n"
            "Por defecto: Nepal y los últimos 15 días (con ~2 días de "
            "desfase; CHIRPS no es tiempo real).\n"
            "Fuente: UCSB Climate Hazards Group. Cobertura 50°S–50°N."
        )

    def flags(self):
        return _no_threading_flag()

    def initAlgorithm(self, config=None):
        self._paises = lista_paises()
        inicio_qdt, fin_qdt = fechas_defecto_qdt()
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PAIS,
                self.tr("País"),
                options=self._paises,
                defaultValue=indice_pais(self._paises),
                allowMultiple=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.FECHA_INICIO,
                self.tr("Inicio (UTC, un día = un raster)"),
                type=_datetime_type(),
                defaultValue=inicio_qdt,
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.FECHA_FIN,
                self.tr("Fin (UTC)"),
                type=_datetime_type(),
                defaultValue=fin_qdt,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BUFFER_KM,
                self.tr("Buffer alrededor del país (km)"),
                type=_number_integer(),
                defaultValue=200,
                minValue=0,
                maxValue=1000,
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.OUTPUT_DIR,
                self.tr("Carpeta donde crear el proyecto"),
                behavior=_folder_behavior(),
                optional=True,
                defaultValue=carpeta_padre_defecto(),
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_OSM,
                self.tr("Añadir OpenStreetMap"),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_FRONTERA,
                self.tr("Añadir frontera del país + buffer"),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.FPS,
                self.tr("Fotogramas por segundo del video"),
                type=_number_integer(),
                defaultValue=4,
                minValue=1,
                maxValue=30,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.ANCHO,
                self.tr("Ancho de exportación (px)"),
                type=_number_integer(),
                defaultValue=1280,
                minValue=400,
                maxValue=4000,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.EXPORT_PNG,
                self.tr("Exportar secuencia PNG"),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.EXPORT_GIF,
                self.tr("Exportar GIF"),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.EXPORT_VIDEO,
                self.tr("Generar video MP4 (ffmpeg)"),
                defaultValue=True,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        idx = self.parameterAsEnum(parameters, self.PAIS, context)
        paises = getattr(self, "_paises", None) or lista_paises()
        if idx < 0 or idx >= len(paises):
            raise QgsProcessingException("País no válido.")
        pais = paises[idx]
        inicio = qdatetime_a_utc(
            self.parameterAsDateTime(parameters, self.FECHA_INICIO, context)
        )
        fin = qdatetime_a_utc(
            self.parameterAsDateTime(parameters, self.FECHA_FIN, context)
        )
        if inicio is None or fin is None:
            raise QgsProcessingException("Indica fecha de inicio y fin en UTC.")

        try:
            resultado = ejecutar_completo(
                pais=pais,
                fecha_inicio=inicio,
                fecha_fin=fin,
                buffer_km=self.parameterAsInt(parameters, self.BUFFER_KM, context),
                output_dir=self.parameterAsFile(parameters, self.OUTPUT_DIR, context),
                add_osm=self.parameterAsBoolean(parameters, self.ADD_OSM, context),
                add_frontera=self.parameterAsBoolean(
                    parameters, self.ADD_FRONTERA, context
                ),
                fps=self.parameterAsInt(parameters, self.FPS, context),
                ancho=self.parameterAsInt(parameters, self.ANCHO, context),
                export_png=self.parameterAsBoolean(
                    parameters, self.EXPORT_PNG, context
                ),
                export_gif=self.parameterAsBoolean(
                    parameters, self.EXPORT_GIF, context
                ),
                export_video=self.parameterAsBoolean(
                    parameters, self.EXPORT_VIDEO, context
                ),
                feedback=feedback,
            )
        except QgsProcessingException:
            raise
        except Exception as e:
            raise QgsProcessingException(str(e))
        return resultado


def run():
    parent = _parent_window()
    try:
        resultado = ejecutar_completo()
    except Exception as e:
        QMessageBox.critical(parent, "Error CHIRPS", str(e))
        raise

    video = resultado.get("video") or "(sin MP4)"
    QMessageBox.information(
        parent,
        "CHIRPS terminado",
        "Proceso terminado.\n\n"
        "País: " + str(resultado.get("pais") or PAIS_DEF) + "\n"
        "Días: " + str(resultado.get("frames") or resultado.get("frames_descarga")) + "\n"
        "PNG: " + str(resultado.get("pngs") or 0) + "\n"
        "Video: " + str(video) + "\n\n"
        "Carpeta:\n" + str(resultado.get("directorio") or ""),
    )
    return resultado


if __name__ == "__main__":
    run()

# ============================================================
# IMERG EARLY RUN — QGIS 3.40 Processing
#
# País + buffer + rango de fechas UTC / intervalo 30 minutos
# (Nepal y últimas 72 h son los valores por defecto)
#
# Precipitación horaria: promedio de los 2 rasters de 30 min
# de esa hora (ej. 08:00 = 15 mm, 08:30 = 40 mm → 27.5 mm).
# El acumulado del periodo es la suma de esos promedios
# (herramienta «IMERG acumulado al clic»).
#
# Cómo usarlo
# 1. Caja de herramientas → Scripts → NASA IMERG
#    → IMERG NASA por país y fechas
# 2. O desde la consola de Python de QGIS:
#    exec(open(r"RUTA\Nepal.py", encoding="utf-8").read())
# ============================================================

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QDate,
    QTime,
    QDateTime,
    Qt,
)
from qgis.PyQt.QtGui import QColor, QPainter, QImage, QFont
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
    QgsMultiBandColorRenderer,
)

import os
import io
import re
import glob
import json
import ssl
import sys
import shutil
import subprocess
import importlib.util
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone


# ------------------------------------------------------------
# Configuración por defecto
# ------------------------------------------------------------

HORAS_DEF = 72
INTERVALO_MINUTOS = 30
OPACIDAD = 75
MAX_FRAMES = 720

WEST = 79.0
EAST = 92.0
SOUTH = 24.0
NORTH = 33.0
BUFFER_METROS = 200000
PAIS_DEF = "Nepal"

PAISES_FALLBACK = [
    "Nepal", "India", "China", "Bhutan", "Bangladesh", "Pakistan",
    "Costa Rica", "Nicaragua", "Honduras", "El Salvador", "Guatemala",
    "Belize", "Panama", "Mexico", "Colombia", "Ecuador", "Peru",
    "Bolivia", "Chile", "Argentina", "Brazil", "Paraguay", "Uruguay",
    "United States of America", "Canada", "Spain", "Portugal",
    "Kenya", "Ethiopia", "Tanzania", "Indonesia", "Philippines",
    "Vietnam", "Thailand", "Myanmar", "Japan", "Australia",
]

# El ImageServer GES DISC (GPM_3IMERGHHE) solo entrega píxeles válidos
# cerca de 00:00 UTC; el resto sale 100 % NoData. Para ver la lluvia
# se usa GIBS WMS (IMERG Early v7, 30 min), pensado para visualización.
GIBS_WMS = (
    "https://gibs.earthdata.nasa.gov/wms/epsg4326/nrt/wms.cgi"
)
GIBS_LAYER = "IMERG_Precipitation_Rate_30min_v7_NRT"
SERVICE_URL = GIBS_WMS
MIN_BYTES_VALIDO = 4000

COUNTRIES_URL = (
    "https://raw.githubusercontent.com/"
    "datasets/geo-countries/master/data/countries.geojson"
)

USER_AGENT = "QGIS-3.40-IMERG-Nepal"
SSL_CONTEXT = ssl.create_default_context()


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
    return os.path.join(os.path.expanduser("~"), "IMERG_NASA")


def carpeta_imerg(pais):
    """Compat: carpeta padre por defecto (ya no es el proyecto)."""
    return carpeta_padre_defecto()


def nombre_proyecto(pais, inicio, fin):
    slug = slug_pais(pais)
    a = inicio.strftime("%Y%m%d")
    b = fin.strftime("%Y%m%d")
    if a == b:
        return "IMERG_{}_{}".format(slug, a)
    return "IMERG_{}_{}_{}".format(slug, a, b)


def preparar_proyecto(padre, pais, inicio, fin, log=None):
    """
    padre/
      IMERG_Pais_inicio_fin/
        frames/   GeoTIFF
        png/      secuencia PNG
        IMERG_Pais.mp4 / .gif   (en la raíz del proyecto)
    """
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


def alinear_30min(dt):
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return dt.replace(minute=(dt.minute // INTERVALO_MINUTOS) * INTERVALO_MINUTOS)


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
    return alinear_30min(py)


def fechas_defecto_qdt():
    fin = alinear_30min(datetime.now(timezone.utc))
    inicio = fin - timedelta(hours=HORAS_DEF)

    def make(dt):
        qdt = QDateTime(
            QDate(dt.year, dt.month, dt.day),
            QTime(dt.hour, dt.minute),
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


def tamano_getmap(west, south, east, north, lado=800):
    aw = max(float(east) - float(west), 0.01)
    ah = max(float(north) - float(south), 0.01)
    if aw >= ah:
        return "{},{}".format(lado, max(int(lado * ah / aw), 200))
    return "{},{}".format(max(int(lado * aw / ah), 200), lado)


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
    patron = os.path.join(os.path.dirname(pngs[0]), "imerg_%04d.png")
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        str(max(int(fps), 1)),
        "-i",
        patron,
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        video_path,
    ]
    log("Generando video MP4 con ffmpeg...")
    proc = subprocess.run(cmd, **kwargs)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[-800:]
        raise RuntimeError("ffmpeg falló:\n" + err)
    log("Video: " + video_path)
    return video_path


PATRON_FRAME = re.compile(r"IMERG_(\d{8})_(\d{4})\.tif$", re.IGNORECASE)


def _listar_tiffs(carpeta, t0=None, t1=None):
    archivos = []
    for ruta in glob.glob(os.path.join(carpeta, "IMERG_*.tif")):
        m = PATRON_FRAME.match(os.path.basename(ruta))
        if not m:
            continue
        tiempo = datetime.strptime(
            m.group(1) + m.group(2), "%Y%m%d%H%M"
        ).replace(tzinfo=timezone.utc)
        if t0 is not None and t1 is not None:
            a, b = t0, t1
            if getattr(a, "tzinfo", None) is None:
                a = a.replace(tzinfo=timezone.utc)
            if getattr(b, "tzinfo", None) is None:
                b = b.replace(tzinfo=timezone.utc)
            if not (min(a, b) <= tiempo <= max(a, b)):
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
    if src is None or src.RasterCount < 3:
        if src is not None:
            src = None
        return None
    ds = gdal.Translate("", src, format="MEM", width=int(ancho), height=int(alto))
    src = None
    r = ds.GetRasterBand(1).ReadAsArray()
    g = ds.GetRasterBand(2).ReadAsArray()
    b = ds.GetRasterBand(3).ReadAsArray()
    if ds.RasterCount >= 4:
        a = ds.GetRasterBand(4).ReadAsArray()
    else:
        a = np.full(r.shape, 255, dtype=np.uint8)
    ds = None
    vacio = (
        ((r <= 20) & (g <= 20) & (b <= 20))
        | ((r >= 230) & (g >= 230) & (b >= 230))
        | (a == 0)
    )
    return r, g, b, ~vacio


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
    """PNG en png/; GIF y MP4 en la raíz del proyecto."""
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
            "No hay GeoTIFF IMERG para exportar en:\n" + frames_dir
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
            feedback.setProgressText("PNG " + tiempo.strftime("%Y-%m-%d %H:%M UTC"))
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
        _dibujar_reloj_pil(img, tiempo.strftime("%Y-%m-%d %H:%M UTC"))
        png_path = os.path.join(png_dir, "imerg_{:04d}.png".format(i + 1))
        if export_png or export_gif or export_video:
            img.save(png_path, "PNG")
            pngs.append(png_path)

    log("PNG exportados: " + str(len(pngs)))
    gif_path = ""
    if export_gif and pngs:
        try:
            gif_path = os.path.join(video_dir, "IMERG_{}.gif".format(slug_pais(pais)))
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
        video_path = os.path.join(video_dir, "IMERG_{}.mp4".format(slug_pais(pais)))
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
    """iface solo existe en la GUI; Processing no lo inyecta."""
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
    """OSM debe quedar debajo de IMERG; si no, tapa toda la lluvia."""
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
        # En QGIS 3.40 takeChild(nodo) devuelve bool, no el nodo.
        cloned = node.clone()
        parent.removeChildNode(node)
        parent.addChildNode(cloned)


def zoom_a_nepal(iface, project):
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
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=SSL_CONTEXT,
    ) as response:
        return response.read()


def descargar_json(url, timeout=60):
    texto = descargar_bytes(url, timeout=timeout).decode("utf-8")
    return json.loads(texto)


def es_tiff(data):
    return data[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def nombre_pais(feature):
    for campo in ("name", "ADMIN", "NAME", "NAME_EN", "NAME_LONG"):
        if campo in feature.fields().names():
            valor = feature[campo]
            if valor:
                return str(valor).strip().lower()
    return ""


def png_a_geotiff(png_path, tif_path):
    """Georreferencia el PNG de GIBS como GeoTIFF RGB (WGS84)."""
    from osgeo import gdal
    gdal.UseExceptions()
    gdal.Translate(
        tif_path,
        png_path,
        format="GTiff",
        outputBounds=[WEST, NORTH, EAST, SOUTH],
        outputSRS="EPSG:4326",
        creationOptions=["COMPRESS=LZW", "TILED=YES"],
    )


def solicitar_frame(service_url, tiempo, salida, bbox, size):
    ancho, alto = size.split(",")
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": GIBS_LAYER,
        "CRS": "EPSG:4326",
        "BBOX": f"{SOUTH},{WEST},{NORTH},{EAST}",
        "WIDTH": ancho.strip(),
        "HEIGHT": alto.strip(),
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
        "INTERPOLATION": "NEAREST",
        "TIME": tiempo.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    url = GIBS_WMS + "?" + urllib.parse.urlencode(params)
    data = descargar_bytes(url, timeout=180)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        preview = data[:400].decode("utf-8", errors="replace")
        raise RuntimeError(
            "GIBS no devolvió un PNG de precipitación.\n"
            + url
            + "\n\n"
            + preview
        )

    png_tmp = salida + ".png"
    with open(png_tmp, "wb") as f:
        f.write(data)
    try:
        png_a_geotiff(png_tmp, salida)
        asegurar_alpha_lluvia(salida)
    finally:
        if os.path.exists(png_tmp):
            os.remove(png_tmp)

    if not os.path.exists(salida) or os.path.getsize(salida) < MIN_BYTES_VALIDO:
        raise RuntimeError("GeoTIFF GIBS demasiado pequeño: " + salida)

    return url


def necesita_descarga(path):
    if not os.path.exists(path):
        return True
    tam = os.path.getsize(path)
    if tam < MIN_BYTES_VALIDO:
        return True
    # GeoTIFF científico vacío/parcial del ImageServer (~1 KB o ~2.3 MB)
    if tam <= 1200 or (2_000_000 <= tam <= 2_400_000):
        return True
    return False


def asegurar_alpha_lluvia(tif_path):
    """Alpha 0 donde no hay lluvia (blanco o negro)."""
    from osgeo import gdal
    import numpy as np
    gdal.UseExceptions()
    ds = gdal.Open(tif_path, gdal.GA_Update)
    if ds is None or ds.RasterCount < 3:
        if ds is not None:
            ds = None
        return
    r = ds.GetRasterBand(1).ReadAsArray()
    g = ds.GetRasterBand(2).ReadAsArray()
    b = ds.GetRasterBand(3).ReadAsArray()
    mask = (
        ((r >= 230) & (g >= 230) & (b >= 230))
        | ((r <= 20) & (g <= 20) & (b <= 20))
    )
    alpha = np.where(mask, 0, 255).astype(np.uint8)
    if ds.RasterCount >= 4:
        band = ds.GetRasterBand(4)
        band.WriteArray(alpha)
        band.SetColorInterpretation(gdal.GCI_AlphaBand)
        band.FlushCache()
        ds.FlushCache()
        ds = None
        return
    ds = None
    tmp = tif_path + ".rgba.tif"
    src = gdal.Open(tif_path, gdal.GA_ReadOnly)
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(
        tmp,
        src.RasterXSize,
        src.RasterYSize,
        4,
        gdal.GDT_Byte,
        options=["COMPRESS=LZW", "TILED=YES", "PHOTOMETRIC=RGB", "ALPHA=YES"],
    )
    out.SetGeoTransform(src.GetGeoTransform())
    out.SetProjection(src.GetProjection())
    out.GetRasterBand(1).WriteArray(r)
    out.GetRasterBand(2).WriteArray(g)
    out.GetRasterBand(3).WriteArray(b)
    out.GetRasterBand(4).WriteArray(alpha)
    out.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    out.FlushCache()
    out = None
    src = None
    os.replace(tmp, tif_path)


def aplicar_simbologia(raster):
    if raster.bandCount() >= 3:
        renderer = QgsMultiBandColorRenderer(
            raster.dataProvider(), 1, 2, 3
        )
        raster.setRenderer(renderer)
        transparency = QgsRasterTransparency()

        def pixel_rgb(rmin, rmax, gmin, gmax, bmin, bmax):
            try:
                return QgsRasterTransparency.TransparentThreeValuePixel(
                    rmin, rmax, gmin, gmax, bmin, bmax, 100.0
                )
            except TypeError:
                p = QgsRasterTransparency.TransparentThreeValuePixel()
                p.minRed = rmin
                p.maxRed = rmax
                p.minGreen = gmin
                p.maxGreen = gmax
                p.minBlue = bmin
                p.maxBlue = bmax
                p.percentTransparent = 100
                return p

        transparency.setTransparentThreeValuePixelList([
            pixel_rgb(0, 25, 0, 25, 0, 25),
            pixel_rgb(230, 255, 230, 255, 230, 255),
        ])
        if renderer is not None and hasattr(renderer, "setRasterTransparency"):
            renderer.setRasterTransparency(transparency)
        if hasattr(renderer, "setAlphaBand") and raster.bandCount() >= 4:
            renderer.setAlphaBand(4)
        try:
            modo = getattr(QPainter, "CompositionMode_SourceOver", None)
            if modo is None:
                modo = QPainter.CompositionMode.SourceOver
            raster.setBlendMode(modo)
        except Exception:
            pass
        raster.triggerRepaint()
        return

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
        item(0.1, 0, 118, 78, "0.1 mm/h"),
        item(0.2, 0, 151, 31, "0.2 mm/h"),
        item(0.5, 78, 195, 0, "0.5 mm/h"),
        item(1, 195, 228, 0, "1 mm/h"),
        item(2, 255, 176, 6, "2 mm/h"),
        item(3, 255, 122, 26, "3 mm/h"),
        item(5, 255, 66, 51, "5 mm/h"),
        item(8, 255, 1, 1, "8 mm/h"),
        item(10, 231, 0, 0, "10 mm/h"),
        item(15, 181, 0, 0, "15 mm/h"),
        item(25, 156, 0, 0, "25 mm/h"),
        item(35, 130, 0, 0, "35 mm/h"),
        item(45, 113, 0, 0, "45 mm/h"),
        item(55, 90, 0, 20, "55 mm/h"),
        item(65, 75, 0, 40, "65 mm/h"),
        item(75, 63, 0, 70, "75 mm/h"),
        item(85, 50, 0, 100, "85 mm/h"),
        item(95, 38, 0, 130, "95 mm/h"),
    ])

    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(
        raster.dataProvider(),
        1,
        shader,
    )

    transparency = QgsRasterTransparency()
    transparency.setTransparentSingleValuePixelList([
        QgsRasterTransparency.TransparentSingleValuePixel(0.0, 0.0, 100.0)
    ])
    renderer.setRasterTransparency(transparency)

    raster.setRenderer(renderer)
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


def activar_temporal(raster, tiempo, minutos=INTERVALO_MINUTOS):
    """Activa el Controlador temporal de QGIS en un raster IMERG."""
    inicio = qdatetime_utc(tiempo)
    fin = qdatetime_utc(tiempo + timedelta(minutes=minutos))
    props = raster.temporalProperties()
    props.setMode(_modo_rango_fijo())
    props.setFixedTemporalRange(QgsDateTimeRange(inicio, fin, True, False))
    props.setIsActive(True)


def _intervalo_minutos(n):
    unidad = getattr(Qgis, "TemporalUnit", None)
    if unidad is not None and hasattr(unidad, "Minutes"):
        return QgsInterval(n, unidad.Minutes)
    return QgsInterval(float(n) * 60.0)


def configurar_controlador_temporal(capas, fps=4.0):
    """Prepara el panel Controlador temporal para pulsar Play."""
    iface = _iface()
    if iface is None or not capas:
        return

    t0 = capas[0][0]
    t1 = capas[-1][0] + timedelta(minutes=INTERVALO_MINUTOS)
    nav = iface.mapCanvas().temporalController()
    nav.setTemporalExtents(
        QgsDateTimeRange(qdatetime_utc(t0), qdatetime_utc(t1), True, False)
    )
    nav.setFrameDuration(_intervalo_minutos(INTERVALO_MINUTOS))
    nav.setFramesPerSecond(float(fps))

    modo = QgsTemporalNavigationObject.Animated
    nav_mode = getattr(QgsTemporalNavigationObject, "NavigationMode", None)
    if nav_mode is not None and hasattr(nav_mode, "Animated"):
        modo = nav_mode.Animated
    nav.setNavigationMode(modo)
    nav.rewindToStart()


def asegurar_osm(project=None, root=None, log=None):
    """Añade OSM si no está. La animación también lo necesita como fondo."""
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


def asegurar_nepal(
    project=None,
    root=None,
    output_dir=None,
    log=None,
    pais=PAIS_DEF,
    buffer_metros=None,
):
    """Añade frontera del país y buffer si no están."""
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
                    max(-90.0, rect.yMinimum()),
                    min(180.0, rect.xMaximum()),
                    min(90.0, rect.yMaximum()),
                )
                break
        return True

    if output_dir is None or str(output_dir).strip() == "":
        output_dir = carpeta_imerg(pais)
    os.makedirs(output_dir, exist_ok=True)

    feat_pais = None
    paises = None
    geojson_path = os.path.join(output_dir, "countries.geojson")
    fallback = os.path.join(
        os.path.expanduser("~"),
        "IMERG_NASA_Nepal_Tibet",
        "countries.geojson",
    )
    try:
        if not os.path.exists(geojson_path) or os.path.getsize(geojson_path) < 1000:
            if os.path.exists(fallback) and os.path.getsize(fallback) > 1000:
                geojson_path = fallback
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
        max(-90.0, rect.yMinimum()),
        min(180.0, rect.xMaximum()),
        min(90.0, rect.yMaximum()),
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


def ejecutar_imerg(
    horas=HORAS_DEF,
    output_dir=None,
    add_osm=True,
    add_nepal=True,
    feedback=None,
    pais=PAIS_DEF,
    fecha_inicio=None,
    fecha_fin=None,
    buffer_km=200,
    cargar_capas=True,
):
    """Descarga frames IMERG para un país y un rango UTC."""

    pais = (pais or PAIS_DEF).strip()
    buf_m = int(float(buffer_km) * 1000)

    if fecha_inicio is not None and fecha_fin is not None:
        inicio = alinear_30min(fecha_inicio)
        fin = alinear_30min(fecha_fin)
    else:
        fin = alinear_30min(datetime.now(timezone.utc))
        inicio = fin - timedelta(hours=int(horas))
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
        t += timedelta(minutes=INTERVALO_MINUTOS)

    if len(tiempos) > MAX_FRAMES:
        raise QgsProcessingException(
            "Demasiados frames ({}). El máximo es {} (unos {} días a 30 min).".format(
                len(tiempos),
                MAX_FRAMES,
                MAX_FRAMES // 48,
            )
        )

    project = QgsProject.instance()
    root = project.layerTreeRoot()

    log("=" * 75)
    log("NASA IMERG — " + pais)
    log("=" * 75)
    log("Directorio: " + output_dir)
    log("Servicio: " + SERVICE_URL)
    log("Capa GIBS: " + GIBS_LAYER)
    log("Inicio UTC: " + inicio.strftime("%Y-%m-%d %H:%M"))
    log("Fin UTC:    " + fin.strftime("%Y-%m-%d %H:%M"))
    log("Frames:     " + str(len(tiempos)))

    grupo_nombre = "IMERG NASA - " + pais
    for nombre in (grupo_nombre, "IMERG NASA 72h - Nepal Tibet"):
        grupo_anterior = root.findGroup(nombre)
        if grupo_anterior:
            root.removeChildNode(grupo_anterior)
    grupo = root.addGroup(grupo_nombre)

    if add_osm:
        asegurar_osm(project, root, log)
    if add_nepal:
        asegurar_nepal(
            project,
            root,
            output_dir,
            log,
            pais=pais,
            buffer_metros=buf_m,
        )

    bbox = f"{WEST},{SOUTH},{EAST},{NORTH}"
    size = tamano_getmap(WEST, SOUTH, EAST, NORTH)

    progress = None
    if feedback is None:
        progress = QProgressDialog(
            "Consultando NASA IMERG...",
            "Cancelar",
            0,
            len(tiempos),
            _parent_window(),
        )
        progress.setWindowTitle("IMERG - " + pais)
        progress.setMinimumDuration(0)

    capas = []
    errores = 0

    for i, tiempo in enumerate(tiempos):
        if cancelado():
            log("Proceso cancelado.")
            break
        if progress is not None:
            progress.setValue(i)
            progress.setLabelText(
                "IMERG " + tiempo.strftime("%Y-%m-%d %H:%M UTC")
            )
            if progress.wasCanceled():
                log("Proceso cancelado.")
                break

        if feedback is not None and len(tiempos) > 0:
            feedback.setProgress(int(50 * i / len(tiempos)))
            feedback.setProgressText(
                "Descarga " + tiempo.strftime("%Y-%m-%d %H:%M UTC")
            )

        nombre_archivo = "IMERG_" + tiempo.strftime("%Y%m%d_%H%M") + ".tif"
        salida = os.path.join(frames_dir, nombre_archivo)

        if necesita_descarga(salida):
            try:
                url = solicitar_frame(SERVICE_URL, tiempo, salida, bbox, size)
                log(tiempo.strftime("%Y-%m-%d %H:%M UTC") + "  OK")
                log(url)
            except Exception as e:
                errores += 1
                log("ERROR " + tiempo.strftime("%Y-%m-%d %H:%M UTC") + ": " + str(e))
                continue

        try:
            asegurar_alpha_lluvia(salida)
        except Exception as e:
            log("Alpha " + nombre_archivo + ": " + str(e))

        if not cargar_capas:
            if os.path.exists(salida):
                capas.append((tiempo, None))
            continue

        nombre_capa = "IMERG " + tiempo.strftime("%d-%m %H:%M UTC")
        raster = QgsRasterLayer(salida, nombre_capa)
        if not raster.isValid() or raster.bandCount() < 3:
            errores += 1
            log("Raster inválido o vacío: " + salida)
            continue

        aplicar_simbologia(raster)
        activar_temporal(raster, tiempo)
        raster.setCustomProperty("IMERG/start", tiempo.isoformat())
        raster.setCustomProperty(
            "IMERG/end",
            (tiempo + timedelta(minutes=INTERVALO_MINUTOS)).isoformat(),
        )
        raster.setCustomProperty(
            "IMERG/UTC",
            tiempo.strftime("%Y-%m-%d %H:%M UTC"),
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
    log("Frames generados: " + str(len(capas)))
    if errores:
        log("Frames con error: " + str(errores))
    log("Periodo: " + inicio.strftime("%Y-%m-%d %H:%M UTC")
        + " → " + fin.strftime("%Y-%m-%d %H:%M UTC"))
    log("BBOX: " + bbox)
    log("Carpeta: " + frames_dir)
    log("=" * 75)

    iface = _iface()
    if iface is not None and capas and cargar_capas:
        zoom_a_nepal(iface, project)
        configurar_controlador_temporal(capas)

    return {
        "frames": len(capas),
        "errores": errores,
        "directorio": frames_dir,
        "output_dir": output_dir,
        "pais": pais,
        "inicio": inicio,
        "fin": fin,
        "servicio": SERVICE_URL,
    }


def _modulo_animacion():
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Nepal_animacion.py")
    if not os.path.exists(ruta):
        raise QgsProcessingException(
            "No se encontró Nepal_animacion.py junto a Nepal.py:\n" + ruta
        )
    spec = importlib.util.spec_from_file_location("_imerg_anim_mod", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    """Descarga IMERG, carga capas en QGIS, exporta PNG y genera el video."""
    pais = (pais or PAIS_DEF).strip()
    if fecha_inicio is not None and fecha_fin is not None:
        inicio = alinear_30min(fecha_inicio)
        fin = alinear_30min(fecha_fin)
    else:
        fin = alinear_30min(datetime.now(timezone.utc))
        inicio = fin - timedelta(hours=HORAS_DEF)
    if fin < inicio:
        inicio, fin = fin, inicio

    def log(msg):
        print(msg)
        if feedback is not None:
            feedback.pushInfo(str(msg))

    raiz, frames_dir, png_dir = preparar_proyecto(
        output_dir, pais, inicio, fin, log
    )

    desc = ejecutar_imerg(
        output_dir=raiz,
        add_osm=add_osm,
        add_nepal=add_frontera,
        feedback=feedback,
        pais=pais,
        fecha_inicio=inicio,
        fecha_fin=fin,
        buffer_km=buffer_km,
        cargar_capas=True,
    )
    if desc["frames"] == 0:
        raise QgsProcessingException(
            "No se generó ningún frame IMERG. Revisa el registro y la conexión a NASA."
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


class ImmergNepalAlgorithm(QgsProcessingAlgorithm):
    """Un solo diálogo: país, fechas, descarga, PNG/GIF y video MP4."""

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
        return ImmergNepalAlgorithm()

    def name(self):
        return "imerg_pais_fechas_video"

    def displayName(self):
        return self.tr("IMERG país, fechas y video")

    def group(self):
        return self.tr("NASA IMERG")

    def groupId(self):
        return "nasa_imerg"

    def shortHelpString(self):
        return self.tr(
            "Descarga IMERG (30 min) para el país y el rango UTC, "
            "carga los GeoTIFF en QGIS, exporta PNG y genera GIF/MP4 "
            "con ffmpeg. No hace falta usar la terminal.\n\n"
            "En la carpeta que elijas se crea un proyecto:\n"
            "  IMERG_Pais_inicio_fin/\n"
            "    frames/   GeoTIFF\n"
            "    png/      secuencia PNG\n"
            "    IMERG_Pais.mp4  y/o  .gif  (en la raíz)\n\n"
            "Por defecto: Nepal y las últimas 72 horas.\n"
            "ffmpeg: C:\\ffmpeg\\bin\\ffmpeg.exe\n\n"
            "Precipitación: cada raster es 30 min. La lluvia de una hora "
            "es el promedio de esa hora (ej. 08:00 = 15 mm y 08:30 = 40 mm "
            "→ 27.5 mm). El acumulado es la suma de esos promedios "
            "(script «IMERG acumulado al clic»)."
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
                self.tr("Inicio (UTC)"),
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
            raise QgsProcessingException("Indica fecha/hora de inicio y fin en UTC.")

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
    """Ejecuta el flujo completo desde la consola de Python de QGIS."""
    parent = _parent_window()
    try:
        resultado = ejecutar_completo()
    except Exception as e:
        QMessageBox.critical(parent, "Error NASA IMERG", str(e))
        raise

    video = resultado.get("video") or "(sin MP4)"
    QMessageBox.information(
        parent,
        "IMERG terminado",
        "Proceso terminado.\n\n"
        "País: " + str(resultado.get("pais") or PAIS_DEF) + "\n"
        "Frames: " + str(resultado.get("frames") or resultado.get("frames_descarga")) + "\n"
        "PNG: " + str(resultado.get("pngs") or 0) + "\n"
        "Video: " + str(video) + "\n\n"
        "Carpeta:\n" + str(resultado.get("directorio") or ""),
    )
    return resultado


# Solo al lanzarlo desde la consola (exec). Processing importa el
# módulo con otro __name__ y NO debe descargar nada al arrancar QGIS.
if __name__ == "__main__":
    run()

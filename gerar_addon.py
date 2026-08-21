import hashlib
import json
import os
import re
import shutil
import time
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
PROXY_BASE_URL = "https://drive-proxy.cmckauan.workers.dev"
FILMES_FOLDER_ID = "1twEX01x0SdhtzoK58klrzP8JoDFdx6gW"
SERIES_FOLDER_ID = "1OZutaKMisH1w6W8PqKFpekyfIJpq6Rws"
LANGUAGE = "pt-BR"
REQUEST_TIMEOUT = 12
MAX_RETRIES = 3
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".webm", ".mov"}
GENEROS_FILMES = ["Ação", "Aventura", "Animação", "Cinema TV", "Comédia", "Crime", "Documentário", "Drama", "Família", "Fantasia", "Ficção científica", "Guerra", "História", "Mistério", "Música", "Romance", "Terror", "Thriller"]
GENEROS_SERIES = ["Ação", "Aventura", "Animação", "Comédia", "Crime", "Documentário", "Drama", "Família", "Fantasia", "Kids", "Mistério", "Reality", "Sci-Fi & Fantasy", "Soap", "Talk"]
GENERATED_PATHS = [Path("catalog/movie/meus_filmes"), Path("catalog/series/minhas_series"), Path("meta/movie"), Path("meta/series"), Path("stream/movie"), Path("stream/series")]
CACHE_DIR = Path(".cache")
CACHE_MOVIES_FILE = CACHE_DIR / "tmdb_movies.json"
CACHE_SERIES_FILE = CACHE_DIR / "tmdb_series.json"
CACHE_EPISODES_FILE = CACHE_DIR / "tmdb_episodes.json"
SERIES_IDENTIFICATIONS_FILE = CACHE_DIR / "series_identifications.json"
ARTWORK_ROOT = Path("assets")
PAGES_BASE_URL = "https://cmckauan-rgb.github.io/Stremio-addon"
ARTWORK_VERSION = "v1"
ARTWORK_MAX_BYTES = 15 * 1024 * 1024
ARTWORK_SPECS = {
    "poster": {"width": 342, "quality": 82},
    "background": {"width": 780, "quality": 78},
    "episode": {"width": 480, "quality": 76},
}

if not TMDB_API_KEY:
    raise SystemExit("ERRO CRÍTICO: TMDB_API_KEY não foi encontrada nos Secrets.")

session = requests.Session()
session.headers.update({"User-Agent": "Stremio-Addon-Generator/3.2", "Accept": "application/json, text/html"})
CACHE_TMDB_MOVIES = {}
CACHE_TMDB_SERIES = {}
CACHE_TMDB_EPISODES = {}
SERIES_IDENTIFICATIONS = {}
PASTAS_VISITADAS = set()
ARTWORK_USED_PATHS = set()
ARTWORK_STATS = {"downloaded": 0, "cached": 0, "fallback": 0, "removed": 0}

def carregar_cache(path):
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"⚠️ Não foi possível ler cache {path}: {exc}")
        return {}

def salvar_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

CACHE_TMDB_MOVIES = carregar_cache(CACHE_MOVIES_FILE)
CACHE_TMDB_SERIES = carregar_cache(CACHE_SERIES_FILE)
CACHE_TMDB_EPISODES = carregar_cache(CACHE_EPISODES_FILE)
SERIES_IDENTIFICATIONS = carregar_cache(SERIES_IDENTIFICATIONS_FILE)

def request_json(url, params=None, label="requisição"):
    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    espera = min(float(retry_after), 30) if retry_after else 2 ** tentativa
                except ValueError:
                    espera = 2 ** tentativa
                print(f"⚠️ Rate limit em {label}; aguardando {espera:.1f}s...")
                time.sleep(espera)
                continue
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if tentativa == MAX_RETRIES:
                print(f"❌ {label} falhou após {MAX_RETRIES} tentativas: {exc}")
                return None
            espera = 2 ** (tentativa - 1)
            print(f"⚠️ {label} falhou ({tentativa}/{MAX_RETRIES}): {exc}; tentando novamente em {espera}s...")
            time.sleep(espera)
    return None

def limpar_gerados():
    for path in GENERATED_PATHS:
        if path.exists():
            shutil.rmtree(path)

def normalizar_nome(nome):
    nome = str(nome or "").lower()
    nome = re.sub(r"[\._]+", " ", nome)
    nome = re.sub(r"\s+", " ", nome)
    return nome.strip()

def chave_serie(nome):
    nome = normalizar_nome(nome)
    nome = re.sub(r"(?:^|\s)(?:id|tmdb)\s*[-:=]?\s*\d+\b", " ", nome, flags=re.IGNORECASE)
    nome = re.sub(r"[^\w\s]", " ", nome, flags=re.UNICODE)
    return re.sub(r"\s+", " ", nome).strip()

def is_video_file(name):
    return Path(str(name or "")).suffix.lower() in VIDEO_EXTENSIONS

def construir_stream_url(file_id):
    if not file_id:
        return None
    value = str(file_id)
    return value if value.startswith(("http://", "https://")) else f"{PROXY_BASE_URL}/file/{value}"

def extrair_ano(nome):
    match = re.search(r"\((\d{4})\)", nome or "")
    return match.group(1) if match else None

def extrair_id_forcado(nome):
    match = re.search(r"(?:^|\s|[-_])(?:id|tmdb)\s*[-:=]?\s*(\d+)\b", nome or "", re.IGNORECASE)
    return match.group(1) if match else None

def listar_pasta_proxy(folder_id):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/html"}
    try:
        response = session.post(f"{PROXY_BASE_URL}/", json={"id": folder_id}, headers=headers, timeout=REQUEST_TIMEOUT)
        if response.ok:
            data = response.json()
            items = data.get("files") or data.get("data", {}).get("files") or data.get("items") or []
            if isinstance(items, list):
                return items
    except (requests.RequestException, ValueError) as exc:
        print(f"⚠️ POST / para pasta {folder_id} falhou: {exc}")
    try:
        response = session.get(f"{PROXY_BASE_URL}/folder/{folder_id}", headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        if "application/json" in response.headers.get("Content-Type", "").lower():
            data = response.json()
            items = data.get("files") or data.get("items") or []
            return items if isinstance(items, list) else []
        soup = BeautifulSoup(response.text, "html.parser")
        items = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            text = anchor.get_text(" ", strip=True)
            if "/folder/" in href:
                fid = href.split("/folder/", 1)[1].split("?", 1)[0].split("#", 1)[0]
                items.append({"name": text, "id": fid, "mimeType": "application/vnd.google-apps.folder"})
            elif "/file/" in href or is_video_file(text):
                fid = href.split("/file/", 1)[1].split("?", 1)[0].split("#", 1)[0] if "/file/" in href else href
                items.append({"name": text, "id": fid, "mimeType": "video/unknown"})
        return items
    except (requests.RequestException, ValueError) as exc:
        print(f"❌ Não foi possível listar pasta {folder_id}: {exc}")
        return []

def item_e_pasta(item):
    mime = str(item.get("mimeType", "")).lower()
    return mime == "application/vnd.google-apps.folder" or "google-apps.folder" in mime or mime.endswith("/folder") or mime == "folder" or "directory" in mime

def buscar_arquivos_recursivo(folder_id):
    if folder_id in PASTAS_VISITADAS:
        return []
    PASTAS_VISITADAS.add(folder_id)
    encontrados = []
    for item in listar_pasta_proxy(folder_id):
        item_id = item.get("id")
        nome = item.get("name") or item.get("title") or ""
        if not item_id or not nome:
            continue
        if item_e_pasta(item):
            if item_id != folder_id:
                encontrados.extend(buscar_arquivos_recursivo(item_id))
        elif is_video_file(nome):
            encontrados.append(item)
    return encontrados

def buscar_tmdb_filme(titulo, ano=None, tmdb_id=None):
    cache_key = f"id:{tmdb_id}" if tmdb_id else f"title:{normalizar_nome(titulo)}|year:{ano or ''}"
    if cache_key in CACHE_TMDB_MOVIES:
        return CACHE_TMDB_MOVIES[cache_key]
    if tmdb_id:
        data = request_json(f"https://api.themoviedb.org/3/movie/{tmdb_id}", {"api_key": TMDB_API_KEY, "language": LANGUAGE, "append_to_response": "external_ids"}, f"TMDB filme {tmdb_id}")
        if data:
            CACHE_TMDB_MOVIES[cache_key] = data
            return data
    data = request_json("https://api.themoviedb.org/3/search/movie", {"api_key": TMDB_API_KEY, "query": titulo, "language": LANGUAGE, **({"primary_release_year": ano} if ano else {})}, f"busca TMDB filme '{titulo}'")
    results = data.get("results", []) if data else []
    if not results:
        CACHE_TMDB_MOVIES[cache_key] = None
        return None
    tmdb_id_result = results[0].get("id")
    details = request_json(f"https://api.themoviedb.org/3/movie/{tmdb_id_result}", {"api_key": TMDB_API_KEY, "language": LANGUAGE, "append_to_response": "external_ids"}, f"detalhes TMDB filme {tmdb_id_result}")
    result = details or results[0]
    CACHE_TMDB_MOVIES[cache_key] = result
    return result

def buscar_tmdb_tv(nome_serie, tmdb_id=None):
    cache_key = f"id:{tmdb_id}" if tmdb_id else f"name:{chave_serie(nome_serie)}"
    if cache_key in CACHE_TMDB_SERIES:
        return CACHE_TMDB_SERIES[cache_key]
    if tmdb_id:
        data = request_json(f"https://api.themoviedb.org/3/tv/{tmdb_id}", {"api_key": TMDB_API_KEY, "language": LANGUAGE, "append_to_response": "external_ids"}, f"TMDB série {tmdb_id}")
        if data:
            CACHE_TMDB_SERIES[cache_key] = data
            return data
    data = request_json("https://api.themoviedb.org/3/search/tv", {"api_key": TMDB_API_KEY, "query": nome_serie, "language": LANGUAGE}, f"busca TMDB série '{nome_serie}'")
    results = data.get("results", []) if data else []
    if not results:
        CACHE_TMDB_SERIES[cache_key] = None
        return None
    tv_id = results[0].get("id")
    details = request_json(f"https://api.themoviedb.org/3/tv/{tv_id}", {"api_key": TMDB_API_KEY, "language": LANGUAGE, "append_to_response": "external_ids"}, f"detalhes TMDB série {tv_id}")
    result = details or results[0]
    CACHE_TMDB_SERIES[cache_key] = result
    return result

def buscar_tmdb_episodio(tv_id, temporada, episodio):
    cache_key = f"{tv_id}:{temporada}:{episodio}:{LANGUAGE}"
    if cache_key in CACHE_TMDB_EPISODES:
        return CACHE_TMDB_EPISODES[cache_key]
    data = request_json(f"https://api.themoviedb.org/3/tv/{tv_id}/season/{temporada}/episode/{episodio}", {"api_key": TMDB_API_KEY, "language": LANGUAGE}, f"TMDB episódio {tv_id} S{temporada:02d}E{episodio:02d}")
    if data:
        CACHE_TMDB_EPISODES[cache_key] = data
    return data

def extrair_info_filme(nome_arquivo):
    base = re.sub(r"\.(mkv|mp4|avi|webm|mov)$", "", nome_arquivo, flags=re.IGNORECASE)
    tmdb_id = extrair_id_forcado(base)
    ano = extrair_ano(base)
    titulo = re.sub(r"\(\d{4}\)", "", base)
    titulo = re.sub(r"(?:^|\s|[-_])(?:id|tmdb)\s*[-:=]?\s*\d+\b", "", titulo, flags=re.IGNORECASE)
    return titulo.strip().lstrip("+").strip(), ano, tmdb_id

def extrair_info_serie(nome_arquivo):
    base = re.sub(r"\.(mkv|mp4|avi|webm|mov)$", "", str(nome_arquivo or ""), flags=re.IGNORECASE)
    padroes = [
        re.compile(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b"),
        re.compile(r"(?i)\b(\d{1,2})\s*[xX]\s*(\d{1,3})\b"),
        re.compile(r"(?i)\b(?:temporada|season)\s*(\d{1,2})\D+(?:epis[oó]dio|episode|ep)\s*(\d{1,3})\b"),
    ]
    for pattern in padroes:
        match = pattern.search(base)
        if not match:
            continue
        nome = base[:match.start()].strip(" ._-+")
        nome = re.sub(r"[._]+", " ", nome)
        nome = re.sub(r"\s+", " ", nome).strip()
        if nome:
            return nome, int(match.group(1)), int(match.group(2))
    return None, None, None

def registrar_identificacao_serie(nome_serie, tmdb_id, origem="arquivo"):
    if not nome_serie or not tmdb_id:
        return
    chave = chave_serie(nome_serie)
    tmdb_id = str(tmdb_id)
    anterior = SERIES_IDENTIFICATIONS.get(chave)
    if anterior != tmdb_id:
        SERIES_IDENTIFICATIONS[chave] = tmdb_id
        print(f"🧠 Identificação: {nome_serie} -> TMDB {tmdb_id} ({origem})")

def obter_identificacao_serie(nome_serie):
    return SERIES_IDENTIFICATIONS.get(chave_serie(nome_serie))

def aprender_ids_das_series(arquivos):
    grupos = {}
    for item in arquivos:
        nome_arq = item.get("name") or item.get("title") or ""
        nome_serie, temporada, episodio = extrair_info_serie(nome_arq)
        if not nome_serie:
            continue
        chave = chave_serie(nome_serie)
        grupos[chave] = grupos.get(chave, 0) + 1
        tmdb_id = extrair_id_forcado(nome_arq)
        if tmdb_id:
            registrar_identificacao_serie(nome_serie, tmdb_id, f"S{temporada:02d}E{episodio:02d}")
    print(f"🧠 Arquivos de séries agrupados: {sum(grupos.values())}; séries: {len(grupos)}; associações: {len(SERIES_IDENTIFICATIONS)}")

def imagem_tmdb(path, size):
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else None

def slug_arte(value):
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip())
    return normalized.strip("-") or "sem-id"

def url_arte_local(path):
    return f"{PAGES_BASE_URL}/{path.as_posix()}"

def salvar_arte_local(source_url, relative_folder, name, kind):
    if not source_url:
        return None

    spec = ARTWORK_SPECS[kind]
    fingerprint = hashlib.sha256(
        f"{ARTWORK_VERSION}|{source_url}|{spec['width']}|{spec['quality']}".encode("utf-8")
    ).hexdigest()[:12]
    relative_path = ARTWORK_ROOT / relative_folder / f"{slug_arte(name)}-{fingerprint}.webp"
    ARTWORK_USED_PATHS.add(relative_path)

    if relative_path.exists() and relative_path.stat().st_size > 0:
        ARTWORK_STATS["cached"] += 1
        return url_arte_local(relative_path)

    relative_path.parent.mkdir(parents=True, exist_ok=True)
    for tentativa in range(1, MAX_RETRIES + 1):
        tmp_path = relative_path.with_suffix(relative_path.suffix + ".tmp")
        try:
            response = session.get(
                source_url,
                headers={"Accept": "image/avif,image/webp,image/*,*/*"},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    espera = min(float(retry_after), 30) if retry_after else 2 ** tentativa
                except ValueError:
                    espera = 2 ** tentativa
                print(f"⚠️ Rate limit ao baixar arte; aguardando {espera:.1f}s...")
                time.sleep(espera)
                continue
            response.raise_for_status()
            if len(response.content) > ARTWORK_MAX_BYTES:
                raise ValueError(f"imagem maior que {ARTWORK_MAX_BYTES // (1024 * 1024)} MB")

            with Image.open(BytesIO(response.content)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                if image.width > spec["width"]:
                    target_height = max(1, round(image.height * spec["width"] / image.width))
                    image = image.resize(
                        (spec["width"], target_height),
                        Image.Resampling.LANCZOS,
                    )
                image.save(
                    tmp_path,
                    format="WEBP",
                    quality=spec["quality"],
                    method=4,
                    optimize=True,
                )
            tmp_path.replace(relative_path)
            ARTWORK_STATS["downloaded"] += 1
            return url_arte_local(relative_path)
        except (requests.RequestException, OSError, ValueError) as exc:
            tmp_path.unlink(missing_ok=True)
            if tentativa == MAX_RETRIES:
                print(f"⚠️ Arte mantida no TMDB após {MAX_RETRIES} tentativas: {exc}")
                ARTWORK_STATS["fallback"] += 1
                return source_url
            espera = 2 ** (tentativa - 1)
            print(f"⚠️ Falha ao baixar arte ({tentativa}/{MAX_RETRIES}): {exc}; nova tentativa em {espera}s...")
            time.sleep(espera)

    ARTWORK_STATS["fallback"] += 1
    return source_url

def limpar_artes_obsoletas():
    if not ARTWORK_ROOT.exists():
        return
    for path in ARTWORK_ROOT.rglob("*.webp"):
        if path in ARTWORK_USED_PATHS:
            continue
        path.unlink(missing_ok=True)
        ARTWORK_STATS["removed"] += 1
    for directory in sorted(
        (path for path in ARTWORK_ROOT.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass

def montar_meta_movie(info, titulo_fallback, mid):
    folder = Path("movie") / slug_arte(mid)
    poster_source = imagem_tmdb(info.get("poster_path"), "w500")
    background_source = imagem_tmdb(info.get("backdrop_path"), "w1280")
    return {
        "id": mid,
        "type": "movie",
        "name": info.get("title") or titulo_fallback,
        "poster": salvar_arte_local(poster_source, folder, "poster", "poster"),
        "background": salvar_arte_local(background_source, folder, "background", "background"),
        "description": info.get("overview") or "Sem sinopse disponível.",
        "releaseInfo": (info.get("release_date") or "")[:4],
        "genres": [g.get("name") for g in info.get("genres", []) if g.get("name")],
    }

def montar_meta_series(info, nome_fallback, sid):
    folder = Path("series") / slug_arte(sid)
    poster_source = imagem_tmdb(info.get("poster_path"), "w500")
    background_source = imagem_tmdb(info.get("backdrop_path"), "w1280")
    return {
        "id": sid,
        "type": "series",
        "name": info.get("name") or nome_fallback,
        "poster": salvar_arte_local(poster_source, folder, "poster", "poster"),
        "background": salvar_arte_local(background_source, folder, "background", "background"),
        "description": info.get("overview") or "Sem sinopse disponível.",
        "releaseInfo": (info.get("first_air_date") or "")[:4],
        "genres": [g.get("name") for g in info.get("genres", []) if g.get("name")],
    }

def salvar_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def gerar_catalogos_por_genero(metas, pasta, generos_padrao):
    generos = set(generos_padrao)
    for meta in metas:
        generos.update(g for g in meta.get("genres", []) if g)
    for genero in sorted(generos, key=str.casefold):
        filtradas = [meta for meta in metas if any(g.casefold() == genero.casefold() for g in meta.get("genres", []))]
        salvar_json(Path(pasta) / f"genre={genero}.json", {"metas": filtradas})

def processar_filmes():
    print("\n🎬 --- PROCESSANDO FILMES ---")
    PASTAS_VISITADAS.clear()
    arquivos = buscar_arquivos_recursivo(FILMES_FOLDER_ID)
    print(f"📦 Vídeos encontrados na pasta de filmes: {len(arquivos)}")
    filmes, streams = {}, {}
    ignorados_episodios = 0
    for item in arquivos:
        nome_arq = item.get("name") or item.get("title") or ""
        file_id = item.get("id")
        if not is_video_file(nome_arq) or not file_id:
            continue
        nome_serie, temporada, episodio = extrair_info_serie(nome_arq)
        if nome_serie:
            ignorados_episodios += 1
            print(f"↪️ Episódio encontrado na pasta de filmes; será processado como série: {nome_arq}")
            continue
        titulo, ano, tmdb_id = extrair_info_filme(nome_arq)
        if not titulo:
            continue
        info = buscar_tmdb_filme(titulo, ano, tmdb_id)
        if not info:
            print(f"⚠️ Filme não encontrado no TMDB: {titulo}")
            continue
        imdb_id = info.get("imdb_id") or info.get("external_ids", {}).get("imdb_id")
        mid = imdb_id or f"tmdb:{info.get('id')}"
        filmes[mid] = montar_meta_movie(info, titulo, mid)
        stream = {"name": "Drive Proxy", "title": f"1080p | {nome_arq}", "url": construir_stream_url(file_id)}
        streams.setdefault(mid, [])
        if stream["url"] and not any(s.get("url") == stream["url"] for s in streams[mid]):
            streams[mid].append(stream)
    metas = list(filmes.values())
    for mid, meta in filmes.items():
        salvar_json(Path("meta/movie") / f"{mid}.json", {"meta": meta})
        salvar_json(Path("stream/movie") / f"{mid}.json", {"streams": streams.get(mid, [])})
    salvar_json(Path("catalog/movie/meus_filmes.json"), {"metas": metas})
    gerar_catalogos_por_genero(metas, "catalog/movie/meus_filmes", GENEROS_FILMES)
    print(f"🎬 Filmes únicos: {len(metas)} | Episódios desviados para séries: {ignorados_episodios}")
    return len(metas)

def processar_series():
    print("\n📺 --- PROCESSANDO SÉRIES ---")
    PASTAS_VISITADAS.clear()
    arquivos = buscar_arquivos_recursivo(SERIES_FOLDER_ID)
    print(f"📦 Vídeos de séries encontrados: {len(arquivos)}")
    aprender_ids_das_series(arquivos)
    series_map = {}
    streams_map = {}
    ignorados = 0
    for item in arquivos:
        nome_arq = item.get("name") or item.get("title") or ""
        file_id = item.get("id")
        if not is_video_file(nome_arq) or not file_id:
            continue
        nome_serie, temporada, episodio = extrair_info_serie(nome_arq)
        if not nome_serie:
            ignorados += 1
            print(f"⚠️ Episódio ignorado: {nome_arq}")
            continue
        nome_key = chave_serie(nome_serie)
        tmdb_id_forcado = extrair_id_forcado(nome_arq)
        tmdb_id = tmdb_id_forcado or obter_identificacao_serie(nome_serie)
        if tmdb_id_forcado:
            registrar_identificacao_serie(nome_serie, tmdb_id_forcado, "ID explícito")
        if nome_key not in series_map:
            print(f"🔎 Identificando {nome_serie}" + (f" pelo TMDB ID {tmdb_id}" if tmdb_id else " pelo nome"))
            info = buscar_tmdb_tv(nome_serie, tmdb_id)
            if not info:
                print(f"⚠️ Série não encontrada no TMDB: {nome_serie}")
                continue
            real_tmdb_id = info.get("id")
            if real_tmdb_id:
                registrar_identificacao_serie(nome_serie, real_tmdb_id, "TMDB")
            imdb_id = info.get("external_ids", {}).get("imdb_id")
            sid = imdb_id or f"tmdb:{real_tmdb_id}"
            series_map[nome_key] = {"info": info, "sid": sid, "tmdb_id": real_tmdb_id, "nome_original": nome_serie, "episodes": {}}
        serie = series_map.get(nome_key)
        if not serie:
            continue
        sid = serie["sid"]
        tv_tmdb_id = serie["tmdb_id"]
        ep_key = f"{sid}:{temporada}:{episodio}"
        stream_url = construir_stream_url(file_id)

        # Consulta individual ao TMDB para obter o título oficial e a imagem/still.
        ep_info = buscar_tmdb_episodio(tv_tmdb_id, temporada, episodio) if tv_tmdb_id else None
        titulo_oficial = (ep_info or {}).get("name") or f"Episódio {episodio}"
        still_source = imagem_tmdb((ep_info or {}).get("still_path"), "w780")
        if still_source:
            still = salvar_arte_local(
                still_source,
                Path("series") / slug_arte(sid) / "episodes",
                f"s{temporada:02d}e{episodio:03d}",
                "episode",
            )
        else:
            # Quando o TMDB não possui imagem do episódio, usa a arte horizontal
            # da série (ou o pôster como último recurso) para evitar cartões vazios.
            fallback_path = serie["info"].get("backdrop_path")
            fallback_kind = "background"
            fallback_size = "w1280"
            if not fallback_path:
                fallback_path = serie["info"].get("poster_path")
                fallback_kind = "poster"
                fallback_size = "w500"
            still = salvar_arte_local(
                imagem_tmdb(fallback_path, fallback_size),
                Path("series") / slug_arte(sid),
                fallback_kind,
                fallback_kind,
            )
        overview = (ep_info or {}).get("overview") or ""
        air_date = (ep_info or {}).get("air_date") or None

        if ep_info:
            print(f"   🎞️ S{temporada:02d}E{episodio:02d} → {titulo_oficial}")
        else:
            print(f"   🎞️ S{temporada:02d}E{episodio:02d} → título não encontrado no TMDB")

        if stream_url:
            stream = {"name": "Drive Proxy", "title": f"1080p | S{temporada:02d}E{episodio:02d} | {titulo_oficial}", "url": stream_url}
            streams_map.setdefault(ep_key, [])
            if not any(s.get("url") == stream_url for s in streams_map[ep_key]):
                streams_map[ep_key].append(stream)

        # Campos oficiais de episódio aceitos pelo Stremio.
        serie["episodes"][ep_key] = {
            "id": ep_key,
            "title": titulo_oficial,
            "season": temporada,
            "episode": episodio,
            **({"thumbnail": still} if still else {}),
            **({"overview": overview} if overview else {}),
            **({"released": f"{air_date}T00:00:00.000Z"} if air_date else {}),
        }

    series_por_id = {}
    for serie in series_map.values():
        if serie["sid"] not in series_por_id:
            series_por_id[serie["sid"]] = serie
        else:
            series_por_id[serie["sid"]]["episodes"].update(serie["episodes"])

    metas_catalogo = []
    for sid, serie in series_por_id.items():
        meta_catalogo = montar_meta_series(serie["info"], serie["nome_original"], sid)
        meta_completa = {
            **meta_catalogo,
            "videos": sorted(serie["episodes"].values(), key=lambda ep: (ep["season"], ep["episode"])),
        }
        metas_catalogo.append(meta_catalogo)
        salvar_json(Path("meta/series") / f"{sid}.json", {"meta": meta_completa})

    for ep_key, stream_list in streams_map.items():
        salvar_json(Path("stream/series") / f"{ep_key}.json", {"streams": stream_list})

    # O catálogo inicial contém apenas os dados da série. Os episódios permanecem
    # nos metadados individuais e são baixados somente quando a série é aberta.
    salvar_json(Path("catalog/series/minhas_series.json"), {"metas": metas_catalogo})
    gerar_catalogos_por_genero(metas_catalogo, "catalog/series/minhas_series", GENEROS_SERIES)
    total_episodios = sum(len(serie["episodes"]) for serie in series_por_id.values())
    print(f"📺 Séries únicas: {len(metas_catalogo)} | 🎞️ Episódios: {total_episodios} | 🔗 Streams: {len(streams_map)} | ⚠️ Ignorados: {ignorados}")
    return len(metas_catalogo), total_episodios

def main():
    print("=" * 60)
    print("🚀 GERADOR DO ADDON STREMIO")
    print("=" * 60)
    limpar_gerados()
    filmes = processar_filmes()
    PASTAS_VISITADAS.clear()
    series, episodios = processar_series()
    salvar_cache(CACHE_MOVIES_FILE, CACHE_TMDB_MOVIES)
    salvar_cache(CACHE_SERIES_FILE, CACHE_TMDB_SERIES)
    salvar_cache(CACHE_EPISODES_FILE, CACHE_TMDB_EPISODES)
    salvar_cache(SERIES_IDENTIFICATIONS_FILE, SERIES_IDENTIFICATIONS)
    limpar_artes_obsoletas()
    print("\n" + "=" * 60)
    print("✅ ATUALIZAÇÃO CONCLUÍDA")
    print(f"🎬 Filmes: {filmes}")
    print(f"📺 Séries: {series}")
    print(f"🎞️ Episódios: {episodios}")
    print(f"🧠 Identificações: {len(SERIES_IDENTIFICATIONS)}")
    print(f"📚 Episódios consultados no TMDB: {len(CACHE_TMDB_EPISODES)}")
    print(
        "🖼️ Artes: "
        f"{ARTWORK_STATS['downloaded']} baixadas, "
        f"{ARTWORK_STATS['cached']} em cache, "
        f"{ARTWORK_STATS['fallback']} no fallback TMDB, "
        f"{ARTWORK_STATS['removed']} antigas removidas"
    )
    print("=" * 60)

if __name__ == "__main__":
    main()

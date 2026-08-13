import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ============================================================
# CONFIGURAÇÃO
# ============================================================

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
PROXY_BASE_URL = "https://drive-proxy.cmckauan.workers.dev"

FILMES_FOLDER_ID = "1twEX01x0SdhtzoK58klrzP8JoDFdx6gW"
SERIES_FOLDER_ID = "1OZutaKMisH1w6W8PqKFpekyfIJpq6Rws"

LANGUAGE = "pt-BR"
REQUEST_TIMEOUT = 12
MAX_RETRIES = 3

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".webm", ".mov"}

GENEROS_FILMES = [
    "Ação", "Aventura", "Animação", "Cinema TV", "Comédia", "Crime",
    "Documentário", "Drama", "Família", "Fantasia", "Ficção científica",
    "Guerra", "História", "Mistério", "Música", "Romance", "Terror",
    "Thriller",
]

GENEROS_SERIES = [
    "Ação", "Aventura", "Animação", "Comédia", "Crime", "Documentário",
    "Drama", "Família", "Kids", "Mistério", "Reality",
    "Sci-Fi & Fantasy", "Soap", "Talk",
]

# Diretórios/arquivos que este script possui e pode regenerar.
GENERATED_PATHS = [
    Path("catalog/movie/meus_filmes"),
    Path("catalog/series/minhas_series"),
    Path("meta/movie"),
    Path("meta/series"),
    Path("stream/movie"),
    Path("stream/series"),
]

CACHE_DIR = Path(".cache")
CACHE_MOVIES_FILE = CACHE_DIR / "tmdb_movies.json"
CACHE_SERIES_FILE = CACHE_DIR / "tmdb_series.json"

if not TMDB_API_KEY:
    raise SystemExit("ERRO CRÍTICO: TMDB_API_KEY não foi encontrada nos Secrets.")


# ============================================================
# HTTP / CACHE
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Stremio-Addon-Generator/2.0",
    "Accept": "application/json, text/html",
})

CACHE_TMDB_MOVIES = {}
CACHE_TMDB_SERIES = {}
PASTAS_VISITADAS = set()


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


def request_json(url, params=None, label="requisição"):
    """GET com retry, tratamento de rate limit e erros HTTP."""
    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

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
            print(f"⚠️ {label} falhou ({tentativa}/{MAX_RETRIES}): {exc}; "
                  f"tentando novamente em {espera}s...")
            time.sleep(espera)

    return None


# ============================================================
# UTILITÁRIOS
# ============================================================

def limpar_gerados():
    """Remove somente os artefatos que o gerador controla."""
    for path in GENERATED_PATHS:
        if path.exists():
            print(f"🧹 Limpando {path}")
            shutil.rmtree(path)

    # catalog.json é mantido como espelho do catálogo de filmes.
    # Não removemos para preservar compatibilidade com o repositório atual.


def normalizar_nome(nome):
    nome = str(nome or "").lower()
    nome = re.sub(r"[\._]+", " ", nome)
    nome = re.sub(r"\s+", " ", nome)
    return nome.strip()


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
    match = re.search(r"-(?:\s*id|\s*tmdb)\s*(\d+)", nome or "", re.IGNORECASE)
    return match.group(1) if match else None


# ============================================================
# GOOGLE DRIVE / WORKER
# ============================================================

def listar_pasta_proxy(folder_id):
    """Lista itens de uma pasta pelo POST do Worker, com GET como fallback."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/html",
    }

    try:
        response = session.post(
            f"{PROXY_BASE_URL}/",
            json={"id": folder_id},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if response.ok:
            data = response.json()
            items = (
                data.get("files")
                or data.get("data", {}).get("files")
                or data.get("items")
                or []
            )
            if isinstance(items, list):
                return items
    except (requests.RequestException, ValueError) as exc:
        print(f"⚠️ POST / para pasta {folder_id} falhou: {exc}")

    try:
        response = session.get(
            f"{PROXY_BASE_URL}/folder/{folder_id}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()

        if "application/json" in content_type:
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
                items.append({
                    "name": text,
                    "id": fid,
                    "mimeType": "application/vnd.google-apps.folder",
                })
            elif "/file/" in href or is_video_file(text):
                fid = (
                    href.split("/file/", 1)[1].split("?", 1)[0].split("#", 1)[0]
                    if "/file/" in href
                    else href
                )
                items.append({
                    "name": text,
                    "id": fid,
                    "mimeType": "video/unknown",
                })

        return items

    except (requests.RequestException, ValueError) as exc:
        print(f"❌ Não foi possível listar pasta {folder_id}: {exc}")
        return []


def item_e_pasta(item):
    mime = str(item.get("mimeType", "")).lower()
    return (
        mime == "application/vnd.google-apps.folder"
        or "google-apps.folder" in mime
        or mime.endswith("/folder")
        or mime == "folder"
        or "directory" in mime
    )


def buscar_arquivos_recursivo(folder_id):
    """Busca vídeos em toda a árvore de pastas, evitando ciclos."""
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


# ============================================================
# TMDB
# ============================================================

def buscar_tmdb_filme(titulo, ano=None, tmdb_id=None):
    cache_key = f"id:{tmdb_id}" if tmdb_id else f"title:{normalizar_nome(titulo)}|year:{ano or ''}"

    if cache_key in CACHE_TMDB_MOVIES:
        return CACHE_TMDB_MOVIES[cache_key]

    if tmdb_id:
        data = request_json(
            f"https://api.themoviedb.org/3/movie/{tmdb_id}",
            params={
                "api_key": TMDB_API_KEY,
                "language": LANGUAGE,
                "append_to_response": "external_ids",
            },
            label=f"TMDB filme {tmdb_id}",
        )
        if data:
            CACHE_TMDB_MOVIES[cache_key] = data
            return data

    data = request_json(
        "https://api.themoviedb.org/3/search/movie",
        params={
            "api_key": TMDB_API_KEY,
            "query": titulo,
            "language": LANGUAGE,
            **({"primary_release_year": ano} if ano else {}),
        },
        label=f"busca TMDB filme '{titulo}'",
    )

    results = data.get("results", []) if data else []
    if not results:
        CACHE_TMDB_MOVIES[cache_key] = None
        return None

    tmdb_id_result = results[0].get("id")
    details = request_json(
        f"https://api.themoviedb.org/3/movie/{tmdb_id_result}",
        params={
            "api_key": TMDB_API_KEY,
            "language": LANGUAGE,
            "append_to_response": "external_ids",
        },
        label=f"detalhes TMDB filme {tmdb_id_result}",
    )

    result = details or results[0]
    CACHE_TMDB_MOVIES[cache_key] = result
    return result


def buscar_tmdb_tv(nome_serie, tmdb_id=None):
    cache_key = f"id:{tmdb_id}" if tmdb_id else f"name:{normalizar_nome(nome_serie)}"

    if cache_key in CACHE_TMDB_SERIES:
        return CACHE_TMDB_SERIES[cache_key]

    if tmdb_id:
        data = request_json(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}",
            params={
                "api_key": TMDB_API_KEY,
                "language": LANGUAGE,
                "append_to_response": "external_ids",
            },
            label=f"TMDB série {tmdb_id}",
        )
        if data:
            CACHE_TMDB_SERIES[cache_key] = data
            return data

    data = request_json(
        "https://api.themoviedb.org/3/search/tv",
        params={
            "api_key": TMDB_API_KEY,
            "query": nome_serie,
            "language": LANGUAGE,
        },
        label=f"busca TMDB série '{nome_serie}'",
    )

    results = data.get("results", []) if data else []
    if not results:
        CACHE_TMDB_SERIES[cache_key] = None
        return None

    tv_id = results[0].get("id")
    details = request_json(
        f"https://api.themoviedb.org/3/tv/{tv_id}",
        params={
            "api_key": TMDB_API_KEY,
            "language": LANGUAGE,
            "append_to_response": "external_ids",
        },
        label=f"detalhes TMDB série {tv_id}",
    )

    result = details or results[0]
    CACHE_TMDB_SERIES[cache_key] = result
    return result


# ============================================================
# PARSERS
# ============================================================

def extrair_info_nome_filme(nome_arquivo):
    base = re.sub(
        r"\.(mkv|mp4|avi|webm|mov)$",
        "",
        nome_arquivo,
        flags=re.IGNORECASE,
    )

    tmdb_id = extrair_id_forcado(base)
    ano = extrair_ano(base)

    titulo = re.sub(r"\(\d{4}\)", "", base)
    titulo = re.sub(
        r"-(?:\s*id|\s*tmdb)\s*\d+",
        "",
        titulo,
        flags=re.IGNORECASE,
    )
    titulo = titulo.strip().lstrip("+").strip()

    return titulo, ano, tmdb_id


def extrair_info_serie(nome_arquivo):
    patterns = [
        r"^(.*?)[._\s-]+[sS](\d{1,2})[eE](\d{1,3})(?:\b|[^0-9])",
        r"^(.*?)[._\s-]+(\d{1,2})x(\d{1,3})(?:\b|[^0-9])",
    ]

    for pattern in patterns:
        match = re.search(pattern, nome_arquivo)
        if match:
            nome = re.sub(r"[\._]+", " ", match.group(1))
            nome = re.sub(r"\s+", " ", nome).strip()
            return nome, int(match.group(2)), int(match.group(3))

    return None, None, None


# ============================================================
# METADATA / CATÁLOGOS
# ============================================================

def imagem_tmdb(path, size):
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else None


def montar_meta_movie(info, titulo_fallback, mid):
    return {
        "id": mid,
        "type": "movie",
        "name": info.get("title") or titulo_fallback,
        "poster": imagem_tmdb(info.get("poster_path"), "w500"),
        "background": imagem_tmdb(info.get("backdrop_path"), "w1280"),
        "description": info.get("overview") or "Sem sinopse disponível.",
        "releaseInfo": (info.get("release_date") or "")[:4],
        "genres": [
            g.get("name")
            for g in info.get("genres", [])
            if g.get("name")
        ],
    }


def montar_meta_series(info, nome_fallback, sid):
    return {
        "id": sid,
        "type": "series",
        "name": info.get("name") or nome_fallback,
        "poster": imagem_tmdb(info.get("poster_path"), "w500"),
        "background": imagem_tmdb(info.get("backdrop_path"), "w1280"),
        "description": info.get("overview") or "Sem sinopse disponível.",
        "releaseInfo": (info.get("first_air_date") or "")[:4],
        "genres": [
            g.get("name")
            for g in info.get("genres", [])
            if g.get("name")
        ],
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
        filtradas = [
            meta for meta in metas
            if any(g.casefold() == genero.casefold() for g in meta.get("genres", []))
        ]

        # Apenas UMA representação física do arquivo.
        # O servidor web fará o escaping UTF-8 da URL.
        filename = f"genre={genero}.json"
        salvar_json(
            Path(pasta) / filename,
            {"metas": filtradas},
        )


# ============================================================
# FILMES
# ============================================================

def processar_filmes():
    print("\n🎬 --- PROCESSANDO FILMES ---")

    arquivos = buscar_arquivos_recursivo(FILMES_FOLDER_ID)
    print(f"📦 Vídeos de filmes encontrados: {len(arquivos)}")

    # ID do catálogo -> metadata
    filmes = {}
    streams = {}

    for item in arquivos:
        nome_arq = item.get("name") or item.get("title") or ""
        file_id = item.get("id")

        if not is_video_file(nome_arq) or not file_id:
            continue

        titulo, ano, tmdb_id = extrair_info_nome_filme(nome_arq)
        if not titulo:
            continue

        info = buscar_tmdb_filme(titulo, ano, tmdb_id)
        if not info:
            print(f"⚠️ Filme não encontrado no TMDB: {titulo}")
            continue

        imdb_id = (
            info.get("imdb_id")
            or info.get("external_ids", {}).get("imdb_id")
        )
        mid = imdb_id or f"tmdb:{info.get('id')}"

        meta = montar_meta_movie(info, titulo, mid)

        # Deduplicação por ID. Se houver dois arquivos para o mesmo filme,
        # mantemos uma entrada no catálogo e vários streams no mesmo JSON.
        filmes[mid] = meta

        stream = {
            "name": "Drive Proxy",
            "title": f"1080p | {nome_arq}",
            "url": construir_stream_url(file_id),
        }

        streams.setdefault(mid, [])
        if stream["url"] and not any(
            s.get("url") == stream["url"] for s in streams[mid]
        ):
            streams[mid].append(stream)

        print(f"✅ Filme: {meta['name']} ({mid})")

    metas = list(filmes.values())

    for mid, meta in filmes.items():
        salvar_json(
            Path("meta/movie") / f"{mid}.json",
            {"meta": meta},
        )
        salvar_json(
            Path("stream/movie") / f"{mid}.json",
            {"streams": streams.get(mid, [])},
        )

    catalog_data = {"metas": metas}
    salvar_json(Path("catalog/movie/meus_filmes.json"), catalog_data)

    # Mantém o catalog.json antigo como espelho, caso seja usado fora do manifest.
    salvar_json(Path("catalog.json"), catalog_data)

    gerar_catalogos_por_genero(
        metas,
        "catalog/movie/meus_filmes",
        GENEROS_FILMES,
    )

    print(f"🎬 Filmes únicos no catálogo: {len(metas)}")
    return len(metas)


# ============================================================
# SÉRIES
# ============================================================

def processar_series():
    print("\n📺 --- PROCESSANDO SÉRIES ---")

    # O conjunto de visitados é reiniciado para esta árvore.
    PASTAS_VISITADAS.clear()
    arquivos = buscar_arquivos_recursivo(SERIES_FOLDER_ID)

    print(f"📦 Vídeos de séries encontrados: {len(arquivos)}")

    # Primeiro agrupamos por nome normalizado para reduzir consultas.
    series_map = {}
    streams_map = {}

    for item in arquivos:
        nome_arq = item.get("name") or item.get("title") or ""
        file_id = item.get("id")

        if not is_video_file(nome_arq) or not file_id:
            continue

        nome_serie, temporada, episodio = extrair_info_serie(nome_arq)
        if not nome_serie:
            print(f"⚠️ Não foi possível identificar SxxExx: {nome_arq}")
            continue

        nome_key = normalizar_nome(nome_serie)
        tmdb_id_forcado = extrair_id_forcado(nome_arq)

        if nome_key not in series_map:
            info = buscar_tmdb_tv(nome_serie, tmdb_id_forcado)

            if not info:
                print(f"⚠️ Série não encontrada no TMDB: {nome_serie}")
                continue

            imdb_id = info.get("external_ids", {}).get("imdb_id")
            sid = imdb_id or f"tmdb:{info.get('id')}"

            # Se duas grafias diferentes apontarem para o mesmo TMDB ID,
            # consolidamos posteriormente por sid.
            series_map[nome_key] = {
                "info": info,
                "sid": sid,
                "nome_original": nome_serie,
                "episodes": {},
            }

            print(f"✅ Série: {info.get('name') or nome_serie} ({sid})")

        serie = series_map.get(nome_key)
        if not serie:
            continue

        sid = serie["sid"]
        ep_key = f"{sid}:{temporada}:{episodio}"

        stream = {
            "name": "Drive Proxy",
            "title": f"1080p | S{temporada:02d}E{episodio:02d}",
            "url": construir_stream_url(file_id),
        }

        # Se houver arquivos duplicados para o mesmo episódio,
        # todos os streams ficam disponíveis.
        streams_map.setdefault(ep_key, [])
        if stream["url"] and not any(
            s.get("url") == stream["url"] for s in streams_map[ep_key]
        ):
            streams_map[ep_key].append(stream)

        serie["episodes"][ep_key] = {
            "id": ep_key,
            "title": f"Episódio {episodio}",
            "season": temporada,
            "episode": episodio,
        }

    # Consolida séries por ID real.
    series_por_id = {}

    for serie in series_map.values():
        sid = serie["sid"]

        if sid not in series_por_id:
            series_por_id[sid] = serie
        else:
            # Junta episódios de grafias diferentes da mesma série.
            series_por_id[sid]["episodes"].update(serie["episodes"])

    metas = []

    for sid, serie in series_por_id.items():
        meta = montar_meta_series(
            serie["info"],
            serie["nome_original"],
            sid,
        )

        meta["videos"] = sorted(
            serie["episodes"].values(),
            key=lambda ep: (ep["season"], ep["episode"]),
        )

        metas.append(meta)

        salvar_json(
            Path("meta/series") / f"{sid}.json",
            {"meta": meta},
        )

    for ep_key, stream_list in streams_map.items():
        salvar_json(
            Path("stream/series") / f"{ep_key}.json",
            {"streams": stream_list},
        )

    salvar_json(
        Path("catalog/series/minhas_series.json"),
        {"metas": metas},
    )

    gerar_catalogos_por_genero(
        metas,
        "catalog/series/minhas_series",
        GENEROS_SERIES,
    )

    print(f"📺 Séries únicas no catálogo: {len(metas)}")
    print(f"🎞️ Episódios com stream: {len(streams_map)}")

    return len(metas), len(streams_map)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("🚀 GERADOR DO ADDON STREMIO")
    print("=" * 60)

    # IMPORTANTE:
    # A limpeza ocorre antes da geração para impedir que arquivos
    # antigos continuem acessíveis depois de serem removidos do Drive.
    limpar_gerados()

    filmes = processar_filmes()

    # Reinicia o conjunto de pastas antes de processar a segunda árvore.
    PASTAS_VISITADAS.clear()

    series, episodios = processar_series()

    salvar_cache(CACHE_MOVIES_FILE, CACHE_TMDB_MOVIES)
    salvar_cache(CACHE_SERIES_FILE, CACHE_TMDB_SERIES)

    print("\n" + "=" * 60)
    print("✅ ATUALIZAÇÃO CONCLUÍDA")
    print(f"🎬 Filmes: {filmes}")
    print(f"📺 Séries: {series}")
    print(f"🎞️ Episódios: {episodios}")
    print("=" * 60)


if __name__ == "__main__":
    main()
